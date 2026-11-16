"""Real reference uploads, explicit consumption and independent human feedback."""
import copy
import hashlib
from typing import Annotated, Literal
from uuid import UUID, uuid5
from fastapi import HTTPException
from pydantic import Field, StrictInt, field_validator, model_validator
from sqlalchemy import select, or_, text
from . import models as m, runtime_documents as native
from . import layered_cases_v209 as cases, layered_case_origin_v209 as accepted
from . import layered_case_use_origin_v210 as origin
from .contracts import Contract, digest
from .services import scoped, require, check_version
from .relations import lock_current_actor
from .layered_approval_contract_v175 import original_json
from .layered_case_use_models_v210 import LayeredCaseUseV210 as Use
from .layered_case_use_models_v210 import LayeredCaseUseCommandV210 as Command
from .layered_case_use_models_v210 import LayeredCaseUseFeedbackV210 as Feedback

PREFIX = '/api/workspace/layered-case-uses-v210'
COMMAND_AUDIT = 'layered_case_use.command_v210'
FEEDBACK_AUDIT = 'layered_case_use.feedback_v210'
ID, Hash, Positive = cases.ID, cases.Hash, cases.Positive
need = origin.need
BOUNDARIES = ['本资料是已批准案例的历史人工经验，只供当前任务对照，不能作为当前对象事实',
    '参考不授予规则修改、模型外发、设备操作或审批权限，须走目标任务原独立审批与验收',
    '采用和人工反馈不证明因果收益、现场状态或模型准确率；未自动学习或发布',
    '完整原TXT保留换行和五项原值；本地解析显示会忽略空白行及行首尾空白']


class Selection(Contract):
    expected_task_version: Positive
    expected_approval_version: Positive
    case_id: ID
    expected_case_version: Positive
    revision_id: ID
    revision_hash: Hash
    source_fingerprint: Hash
    applicable_reason: str = Field(strict=True, min_length=1, max_length=2000)

    @field_validator('applicable_reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or '\x00' in value: raise ValueError('请说明本任务为何参考该案例')
        return value


class Create(Selection):
    request_id: ID
    preview_hash: Hash
    boundary_confirmed: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit(cls, value):
        if type(value) is not dict or value.get('boundary_confirmed') is not True:
            raise ValueError('请明确核对历史参考适用范围及限制')
        return value


class Withdraw(Contract):
    request_id: ID
    expected_use_version: Positive
    reason: str = Field(strict=True, min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or '\x00' in value: raise ValueError('请说明本次操作依据')
        return value


class FeedbackCreate(Withdraw):
    consumption_fingerprint: Hash
    acceptance_request_id: ID
    basis_hash: Hash
    evaluation: Literal['helpful', 'not_helpful', 'uncertain']
    human_activation_ids: list[Annotated[str, Field(strict=True, min_length=1, max_length=200)]] = Field(min_length=1, max_length=16)

    @field_validator('human_activation_ids')
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values) or any(not item.strip() or '\x00' in item for item in values):
            raise ValueError('请选择不重复的真实目标人工处理结果')
        return values


CONTRACTS = {'create': Create, 'withdraw': Withdraw, 'feedback': FeedbackCreate, 'preview': Selection}


def parse(raw, operation):
    need(type(raw) is bytes and 0 < len(raw) <= 65536, '案例参考请求为空或超过64KiB，原输入保留', 413)
    try: return original_json(raw, CONTRACTS[operation])
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '案例参考的字段、原请求或固定版本不符合合同；未截断或自动替换') from None


def reference_bytes(reference, target, reason):
    value = cases.Value.model_validate(reference['value']).model_dump()
    lines = ['已批准业务案例历史参考；并非当前任务事实或执行授权。',
        '来源业务：' + reference['business_name'] + '；目标业务：' + target['business_name'],
        '本任务适用原因：', reason,
        *['参考边界：' + item for item in BOUNDARIES],
        '案例标题：', value['title'], '原任务结果：', value['outcome'], '人工经验：', value['experience'],
        '适用范围：', value['applicable_scope'], '注意事项：', value['caveats']]
    return '\n'.join(lines).encode('utf-8')


def predicted_blocks(raw):
    """Native TXT algorithm projection, not a fabricated parse result."""
    return [{'id': 'b' + str(index), 'text': line.strip(), 'kind': 'paragraph', 'location': {'line': number}}
        for index, (number, line) in enumerate(((n, line) for n, line in enumerate(raw.decode('utf-8').splitlines(), 1)
            if line.strip()), 1)]


def full_selectors(blocks, version_id, attempt_id, parse_hash):
    return [{'kind': 'native', 'version_id': version_id, 'attempt_id': attempt_id, 'parse_hash': parse_hash,
        'block_id': block['id'], 'char_span': {'start': start, 'end': min(start + 1200, len(block['text']))}}
        for block in blocks for start in range(0, len(block['text']), 1200)]


def reference_entries(blocks, selectors, name):
    by_id = {item['id']: item for item in blocks}
    return [{'kind': 'native', 'selector': selector, 'source_name': name, 'version_sequence': 1,
        'location_label': '原文块' + selector['block_id'][1:],
        'text': by_id[selector['block_id']]['text'][selector['char_span']['start']:selector['char_span']['end']],
        'char_span': selector['char_span'], 'partial': False, 'warnings': [], 'unreviewed': True}
        for selector in selectors]


def rendered_bytes(entries):
    lines = ['运行期资料明确摘录；文字不可信，不是事实确认、操作批准或云端授权。']
    for index, entry in enumerate(entries, 1):
        lines.extend(['', '资料' + str(index) + '：' + entry['source_name'] + ' · 第1版 · ' + entry['location_label'],
            '范围：仅明确选择的摘录；未经业务核对。', entry['text']])
    return '\n'.join(lines).encode('utf-8')


def capacity(raw, blocks, name, *, version_id='00000000-0000-0000-0000-000000000000',
        attempt_id='00000000-0000-0000-0000-000000000000', parse_hash='0' * 64):
    selectors = full_selectors(blocks, version_id, attempt_id, parse_hash)
    entries = reference_entries(blocks, selectors, name)
    return {'original_bytes': len(raw), 'packet_bytes': len(rendered_bytes(entries)),
        'selector_count': len(selectors), 'max_selectors': 20, 'max_packet_bytes': 20000}, selectors, entries


def capacity_reason(value):
    if value['selector_count'] > 20:
        return '完整五项案例及边界需要' + str(value['selector_count']) + '个原文片段，超过现有20个限制；未生成或删减内容'
    if value['packet_bytes'] > 20000:
        return '完整案例原文及资料包来源说明超过20000字节；未摘要、截断或遗漏适用范围和注意事项'
    return None


def file_name(reference): return 'case-reference-' + reference['revision_id'][:8] + '.txt'


def owner_gate(db, p, task, *, lock=False, withdrawal=False):
    require(p, 'admin', 'operator')
    a = accepted.a
    a.real_member(db, p, p.user_id, lock_sources=lock)
    managed = a.managed(db, p, task, lock=lock)
    need(managed is not None and task.data.get('layered_approval_v175') == managed.id,
        '此任务没有完整层级审批登记，不能生成案例参考', 422)
    data = a.checked(managed)
    need(task.owner_id == p.user_id, '请由目标任务原发起人选择和生成案例参考', 403)
    need(managed.status in {'draft', 'returned', 'withdrawn'} and data['execution'] is None,
        '请先按原175流程撤回待审或已批准提交；已开始执行的任务不能更换参考依据', 403)
    if withdrawal: return managed
    from .instance_api import active
    instance, release = active(db, p, task.data['instance_id'], lock)
    need(release.id == task.data['instance_release_id'] and task.data['business_id'] in instance.data['enabled_business_ids'],
        '目标任务原发布版或业务启用条件已变化；不能生成新的参考')
    _, _, graph = origin.target_pin(db, p, task)
    from .runtime_task_documents import verify_graph_sources
    verify_graph_sources(db, p, graph, current=True)
    policy = a.current_policy(db, p, managed.policy_id, lock_sources=lock)
    _, spec = a.policy_value(db, p, policy, current=True)
    need(spec['scope_id'] == data['scope_id'], '目标任务原组织范围已变化')
    a.ancestors(db, p, spec['scope_id'], lock_sources=lock)
    return managed


def selection(db, p, task_id, body, *, require_current=True):
    task = scoped(db, m.Task, task_id, p)
    managed = accepted.a.managed(db, p, task)
    need(managed is not None, '此任务没有可采用参考的层级审批登记', 422)
    target, target_pin, _ = origin.target_pin(db, p, task)
    _, source_pin, reference = origin.approved_case(db, p, body.case_id)
    source_case = scoped(db, cases.Case, body.case_id, p)
    need(source_case.task_id != task.id, '不能把目标任务自身的案例用作该任务参考', 422)
    need(task.version == body.expected_task_version and managed.version == body.expected_approval_version
        and reference['version'] == body.expected_case_version and reference['revision_id'] == body.revision_id
        and reference['revision_hash'] == body.revision_hash and reference['source_fingerprint'] == body.source_fingerprint,
        '目标任务、原案例或固定修订已变化，请保留适用说明并重新核对')
    raw = reference_bytes(reference, target, body.applicable_reason)
    limit, _, _ = capacity(raw, predicted_blocks(raw), file_name(reference))
    reason = capacity_reason(limit)
    if require_current and reason is None:
        try: owner_gate(db, p, task)
        except HTTPException as error: reason = str(error.detail)
    value = {'schema': 'layered-case-use-preview-v210', 'target': target, 'case': reference,
        'applicable_reason': body.applicable_reason, 'boundaries': copy.deepcopy(BOUNDARIES),
        'reference_text': raw.decode(), 'capacity': limit, 'can_create': reason is None, 'reason': reason}
    # Current permission is a gate, not part of immutable content preview.
    hashed = digest({'selection': {key: body.model_dump()[key] for key in Selection.model_fields},
        'target_pin': target_pin, 'source_pin': source_pin, 'reference_sha256': hashlib.sha256(raw).hexdigest(),
        'capacity': limit, 'boundaries': BOUNDARIES})
    return {**value, 'preview_hash': hashed}, target_pin, source_pin


def seal(row, data): cases.seal(row, data)
def checked(row): return cases.checked(row)


def state(row):
    data = checked(row)
    return {'id': row.id, 'workspace_id': row.workspace_id, 'target_task_id': row.target_task_id,
        'source_case_id': row.source_case_id, 'author_id': row.author_id, 'document_version_id': row.document_version_id,
        'binding_hash': row.binding_hash, 'creation_request_id': row.creation_request_id, 'status': row.status,
        'version': row.version, 'created_at': accepted.utc(row.created_at),
        'data': {key: value for key, value in data.items() if key != 'command_ids'}}


def audit_event(db, p, action, identity, data, actor): return cases.event(db, p, action, identity, data, actor)


def command_proof(row):
    return {'use_id': row.use_id, 'command_id': row.id, 'request_id': row.request_id,
        'target_task_id': row.data['state']['target_task_id'], 'version_id': row.data['state']['document_version_id'],
        'actor_id': row.actor_id, 'operation': row.operation, 'path': row.path,
        'body_sha256': row.body_sha256, 'data_hash': digest(row.data)}


def command_checked(db, p, row):
    value = checked(row)
    need(row.workspace_id == p.workspace_id and row.version == 1 and row.operation in {'create', 'withdraw', 'feedback'}
        and set(value) == {'schema', 'result', 'result_hash', 'state', 'state_hash', 'previous_state_hash'}
        and value['schema'] == 'layered-case-use-command-v210' and value['result_hash'] == digest(value['result'])
        and value['state_hash'] == digest(value['state']))
    body, _, sha = parse(row.exact_body.encode('utf-8'), row.operation)
    need(body.request_id == row.request_id and sha == row.body_sha256
        and value['result']['use_id'] == row.use_id and value['result']['request_id'] == row.request_id
        and value['result']['operation'] == row.operation and value['result']['use_version'] == value['state']['version']
        and value['result']['status'] == value['state']['status'])
    path = (PREFIX + '/tasks/' + value['state']['target_task_id'] + '/uses' if row.operation == 'create'
        else PREFIX + '/uses/' + row.use_id + '/' + row.operation)
    need(row.path == path)
    audit_event(db, p, COMMAND_AUDIT, row.id, command_proof(row), row.actor_id)
    return value, body


def feedback_proof(row):
    return {'id': row.id, 'workspace_id': row.workspace_id, 'use_id': row.use_id, 'actor_id': row.actor_id,
        'request_id': row.request_id, 'acceptance_request_id': row.acceptance_request_id,
        'consumption_fingerprint': row.consumption_fingerprint, 'created_at': accepted.utc(row.created_at),
        'data': copy.deepcopy(row.data)}


def checked_feedback(db, p, row):
    need(row.workspace_id == p.workspace_id and row.version == 1 and row.feedback_hash == digest(feedback_proof(row))
        and row.data.get('schema') == 'layered-case-use-feedback-v210')
    body, _, _ = parse(row.data['exact_body'].encode('utf-8'), 'feedback')
    need(body.request_id == row.request_id and body.acceptance_request_id == row.acceptance_request_id
        and body.consumption_fingerprint == row.consumption_fingerprint
        and row.data['value'] == body.model_dump()
        and row.data['consumption']['accepted'] is True and row.data['consumption']['consumed'] is True
        and row.data['consumption']['consumption_fingerprint'] == row.consumption_fingerprint
        and row.data['consumption']['acceptance']['request_id'] == row.acceptance_request_id
        and row.data['consumption']['acceptance']['basis_hash'] == body.basis_hash
        and {item['activation_id'] for item in row.data['human_results']} == set(body.human_activation_ids)
        and all(item in row.data['consumption']['human_results'] for item in row.data['human_results']))
    audit_event(db, p, FEEDBACK_AUDIT, row.id, {'use_id': row.use_id, 'feedback_id': row.id,
        'request_id': row.request_id, 'target_task_id': row.data['target_task_id'],
        'feedback_hash': row.feedback_hash}, row.actor_id)
    return row


def inventory(db, p, row, *, verify_origin=True):
    data = checked(row)
    need(row.status in {'active', 'withdrawn'} and data.get('schema') == 'layered-case-use-state-v210'
        and data.get('status') == row.status and data.get('binding_hash') == row.binding_hash
        and row.binding_hash == digest(data.get('binding'))
        and type(data.get('command_ids')) is list and len(set(data['command_ids'])) == len(data['command_ids']))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id, Command.use_id == row.id)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND_AUDIT, m.Audit.data['use_id'].as_string() == row.id)))
    need(data['command_ids'] and set(data['command_ids']) == {item.id for item in commands}
        and len(events) == len(commands) and {item.object_id for item in events} == {item.id for item in commands})
    by_id = {item.id: item for item in commands}; previous = None
    for number, identity in enumerate(data['command_ids'], 1):
        command = by_id[identity]; value, body = command_checked(db, p, command); authority = value['state']
        need(authority['id'] == row.id and authority['workspace_id'] == p.workspace_id
            and authority['target_task_id'] == row.target_task_id and authority['source_case_id'] == row.source_case_id
            and authority['binding_hash'] == row.binding_hash and authority['document_version_id'] == row.document_version_id
            and authority['author_id'] == row.author_id and authority['creation_request_id'] == row.creation_request_id
            and authority['version'] == number
            and value['previous_state_hash'] == (digest(previous) if previous is not None else None)
            and (command.operation == 'create') is (number == 1))
        if previous is None:
            need(command.actor_id == row.author_id and body.request_id == row.creation_request_id
                and authority['status'] == 'active')
        else:
            need(body.expected_use_version == previous['version'])
            if command.operation == 'withdraw':
                need(command.actor_id == row.author_id and previous['status'] == 'active' and authority['status'] == 'withdrawn')
            else:
                need(command.operation == 'feedback' and previous['data']['feedback_id'] is None
                    and authority['data']['feedback_id'] is not None and authority['status'] == previous['status'])
        previous = authority
    need(previous == state(row))
    feedbacks = list(db.scalars(select(Feedback).where(Feedback.workspace_id == p.workspace_id, Feedback.use_id == row.id)))
    feedback_events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == FEEDBACK_AUDIT, m.Audit.data['use_id'].as_string() == row.id)))
    need(len(feedbacks) <= 1 and len(feedback_events) == len(feedbacks)
        and {item.object_id for item in feedback_events} == {item.id for item in feedbacks}
        and data.get('feedback_id') == (feedbacks[0].id if feedbacks else None))
    for feedback in feedbacks:
        checked_feedback(db, p, feedback)
        need(feedback.actor_id not in {row.author_id, data['binding']['source_pin']['case_author_id']})
        makers = [item for item in commands if item.request_id == feedback.request_id]
        need(len(makers) == 1 and makers[0].operation == 'feedback' and makers[0].actor_id == feedback.actor_id
            and command_checked(db, p, makers[0])[0]['result']['feedback_id'] == feedback.id)
    if verify_origin: origin.checked_use(db, p, row)
    return data


def reverse_inventory(db, p, task_id=None):
    use_ids = select(Use.id).where(Use.workspace_id == p.workspace_id)
    for cls in (native.Version, m.Evidence):
        orphan = select(cls.id).where(cls.workspace_id == p.workspace_id,
            cls.data[origin.KEY]['use_id'].as_string().is_not(None),
            cls.data[origin.KEY]['use_id'].as_string().not_in(use_ids))
        if task_id is not None: orphan = orphan.where(cls.data[origin.KEY]['target_task_id'].as_string() == task_id)
        need(db.scalar(orphan.limit(1)) is None,
            '参考原件仍有独立采用标记但Use主记录缺失；未当作普通上传或重新生成')
    command_ids = select(Command.id).where(Command.workspace_id == p.workspace_id)
    feedback_ids = select(Feedback.id).where(Feedback.workspace_id == p.workspace_id)
    query = select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, FEEDBACK_AUDIT, origin.GENERATED]), or_(
            m.Audit.data['use_id'].as_string().not_in(use_ids),
            (m.Audit.action == COMMAND_AUDIT) & m.Audit.object_id.not_in(command_ids),
            (m.Audit.action == FEEDBACK_AUDIT) & m.Audit.object_id.not_in(feedback_ids)))
    if task_id is not None: query = query.where(m.Audit.data['target_task_id'].as_string() == task_id)
    need(db.scalar(query.limit(1)) is None, '案例参考已有独立保存痕迹但原库存缺失；未当作空列表或重新生成')


def lock_nonce(db, p, identity):
    key = int(digest(['layered-case-use-v210', p.workspace_id, identity])[:16], 16)
    if key >= 2 ** 63: key -= 2 ** 64
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def negative(db, p, identity):
    need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, FEEDBACK_AUDIT, origin.GENERATED]),
        m.Audit.data['request_id'].as_string() == identity)) is None
        and db.scalar(select(Use.id).where(Use.workspace_id == p.workspace_id, Use.creation_request_id == identity)) is None
        and db.scalar(select(Feedback.id).where(Feedback.workspace_id == p.workspace_id, Feedback.request_id == identity)) is None,
        '原案例参考请求已有成功痕迹但主记录缺失；未生成新参考或改换请求')
    need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == native.COMMAND, m.Audit.object_id == upload_nonce(p, identity))) is None
        and db.scalar(select(native.Version.id).where(native.Version.workspace_id == p.workspace_id,
            native.Version.data[origin.KEY]['request_id'].as_string() == identity)) is None
        and db.scalar(select(m.Evidence.id).where(m.Evidence.workspace_id == p.workspace_id,
            m.Evidence.data[origin.KEY]['request_id'].as_string() == identity)) is None,
        '原请求的真实派生上传或原件仍有保存痕迹；未补建Use或认定不存在')


def prior(db, p, identity, *, raw=None, sha=None, path=None):
    row = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id, Command.request_id == identity))
    if row is None: negative(db, p, identity); return None
    need(row.actor_id == p.user_id, '请由原操作账号核对这项案例参考请求', 403)
    need((raw is None or row.exact_body == raw) and (sha is None or row.body_sha256 == sha)
        and (path is None or row.path == path), '原案例参考请求不能更换正文、账号或操作')
    value, _ = command_checked(db, p, row)
    use = db.scalar(select(Use).where(Use.workspace_id == p.workspace_id, Use.id == row.use_id))
    need(use is not None, '原请求存在但案例参考主记录缺失；未重建')
    inventory(db, p, use)
    return copy.deepcopy(value['result'])


def native_reference(db, p, row, *, attempt_id=None, parse_hash=None):
    version = scoped(db, native.Version, row.document_version_id, p)
    raw = native.checked_version(db, p, version)
    base = {'ready': False, 'selectors': [], 'selector_previews': [],
        'capacity': row.data['binding']['capacity'], 'parse_status': None, 'ack_verified': False,
        'code': 'queued', 'reason': None, 'error': version.data.get('error')}
    identity = attempt_id or version.data.get('latest_attempt_id')
    if not identity:
        return {**base, 'code': 'parsing' if version.status == 'parsing' else 'queued', 'reason': '原参考TXT正在等待本地后台解析'}
    attempt = scoped(db, native.Attempt, identity, p)
    if attempt.status not in native.TERMINAL:
        return {**base, 'code': 'parsing', 'parse_status': attempt.status, 'reason': '正在本地解析完整参考TXT'}
    attempt, result = native.selected_attempt(db, p, version, identity, parse_hash)
    base['parse_status'] = result['status']
    from .runtime_document_receipts import delivery_view
    delivery = delivery_view(db, p, attempt)
    base['ack_verified'] = delivery['verified']
    if result['status'] != 'parsed':
        return {**base, 'code': 'parse_failed', 'reason': '参考TXT尚未完整解析；原件保留，请在原文档页取消或恢复后重新核对',
            'error': result.get('error')}
    need(result['engine'] == 'native-text-v1' and result['blocks'] == predicted_blocks(raw),
        '案例参考TXT的真实完整解析块与原字节无法核对；未改用首段或摘要')
    if not delivery['verified']:
        return {**base, 'code': 'awaiting_ack', 'reason': delivery.get('error') or '解析已完成，等待独立数据收档回执'}
    limit, selectors, entries = capacity(raw, result['blocks'], version.name, version_id=version.id,
        attempt_id=attempt.id, parse_hash=attempt.data['parse_hash'])
    reason = capacity_reason(limit)
    need(reason is None, reason or '')
    return {**base, 'ready': True, 'code': 'ready', 'reason': None, 'ack_verified': True,
        'capacity': limit, 'selectors': selectors,
        'selector_previews': [{'label': item['location_label'], 'text': item['text']} for item in entries]}


def feedback_view(db, row):
    body = row.data['value']
    return {'id': row.id, 'use_id': row.use_id, 'created_at': accepted.utc(row.created_at),
        'actor': accepted.person(db, row.actor_id), 'evaluation': body['evaluation'], 'reason': body['reason'],
        'human_activation_ids': body['human_activation_ids'], 'consumption_fingerprint': row.consumption_fingerprint,
        'acceptance_request_id': row.acceptance_request_id, 'basis_hash': body['basis_hash'], 'readonly': True}


def saved_feedback(db, p, row):
    identity = checked(row)['feedback_id']
    return feedback_view(db, checked_feedback(db, p, scoped(db, Feedback, identity, p))) if identity else None


def feedback_allowed(db, p, row, task, managed, spec, *, lock=False):
    need(checked(row)['feedback_id'] is None, '此采用已有独立反馈，首片只读且不覆盖原评价', 403)
    need(p.user_id not in {row.author_id, row.data['binding']['source_pin']['case_author_id']},
        '效果反馈须独立于采用人和源案例作者，管理员也不能自评', 403)
    accepted.reviewer(db, p, managed, spec, lock=lock)


def consumption(db, p, row, *, verify=True):
    with origin.verification_scope(db):
        if verify: origin.checked_use(db, p, row)
        task = scoped(db, m.Task, row.target_task_id, p)
        managed = accepted.a.managed(db, p, task); need(managed is not None)
        data = accepted.a.checked(managed)
        feedback = saved_feedback(db, p, row)
        result = {'schema': 'layered-case-use-consumption-v210', 'use_id': row.id, 'consumed': False,
            'reason': '尚未通过原任务审批及真实资料包生产；解析和选入资料集不等于实际消费',
            'consumption_fingerprint': None, 'source_sets': [], 'submission': None, 'packets': [],
            'provided_to_model': False, 'model_analysis_verified': False, 'accepted': False, 'acceptance': None,
            'human_results': [], 'permissions': {'feedback': False}, 'blocked_reasons': {'feedback': '目标任务尚未独立验收'},
            'feedback': feedback}
        if data['execution'] is None or data['current_submission_id'] is None: return result
        submission, submitted, spec = accepted.a.current_submission(db, p, managed)
        from . import runtime_task_documents as sources, workflow_document_packet_v207 as packet
        _, graph, _ = sources.task_context(db, p, task)
        selected, proofs = [], []
        for port in sources.ports(graph):
            ref = submitted['value']['graph_inputs'].get(port['key'])
            need(type(ref) is dict)
            source_set = scoped(db, sources.SourceSet, ref['source_set_id'], p)
            need(source_set.source_set_hash == ref['source_set_hash'])
            sources.checked_set(db, p, source_set, task=task, input_key=port['key'])
            use_proofs = origin.checked_coverage(db, p, task, source_set.data['entries'])
            if not any(item['use_id'] == row.id for item in use_proofs): continue
            selected.append(source_set); proofs.extend(use_proofs)
        if not selected:
            return {**result, 'reason': '原启动所用的提交没有完整采用此案例参考；已有其他资料不受影响'}
        selected_ids = {item.id for item in selected}
        packets = list(db.scalars(select(packet.Packet).where(packet.Packet.workspace_id == p.workspace_id,
            packet.Packet.task_id == task.id, packet.Packet.source_set_id.in_(selected_ids)).order_by(packet.Packet.id)))
        if not packets:
            state_value = task.data.get('graph_runtime', {})
            completed_producer = any(activation['status'] == 'completed' and next(node for node in graph['nodes']
                if node['id'] == activation['node_id']).get('execution', {}).get('adapter') == packet.ADAPTER
                for activation in state_value.get('activations', {}).values())
            need(not completed_producer, '原资料包节点已完成但独立库存缺失；未把它改作未消费')
            return result
        packet_proofs = [packet.checked_packet(db, p, item) for item in packets]
        need({item.source_set_id for item in packets} == selected_ids)
        result.update(consumed=True, reason='已作为本任务原批准资料包提供给人工流程；尚未独立验收',
            source_sets=[{'id': item.id, 'source_set_hash': item.source_set_hash} for item in selected],
            submission={'id': submission.id, 'submission_hash': submission.data['record_hash']},
            packets=[{'id': item.id, 'evidence_id': item.evidence_id} for item in packets])
        from . import business_reasoning_v197 as reasoning
        packet_evidence = {item.evidence_id for item in packets}; model_proofs = []
        for activation in task.data.get('graph_runtime', {}).get('activations', {}).values():
            node = next(node for node in graph['nodes'] if node['id'] == activation['node_id'])
            if node.get('execution', {}).get('adapter') != reasoning.ADAPTER or activation['status'] != 'completed': continue
            transport = task.data['graph_jobs'][activation['id']]
            job = scoped(db, m.Job, transport['job_id'], p); call = scoped(db, m.Invocation, transport['invocation_id'], p)
            cap = scoped(db, m.Capability, node['execution']['capability_id'], p)
            saved = reasoning.saved_result(db, p, task, job, call, cap, job.data['inputs'])
            need(saved == activation['output'])
            if packet_evidence.intersection(job.data['inputs']['evidence_ids']):
                model_proofs.append({'invocation_id': call.id, 'data_hash': digest(call.data)})
        result['provided_to_model'] = result['model_analysis_verified'] = bool(model_proofs)
        proof = {'schema': 'layered-case-use-consumption-proof-v210', 'use_id': row.id, 'binding_hash': row.binding_hash,
            'target_task_id': task.id, 'submission_id': submission.id, 'submission_hash': submission.data['record_hash'],
            'source_set_proofs': [{'id': item.id, 'hash': item.source_set_hash} for item in selected],
            'coverage': proofs, 'packet_proofs': packet_proofs, 'model_proofs': model_proofs}
        if managed.status == 'accepted':
            _, _, spec, accepted_proof, source = accepted.accepted_source(db, p, task.id)
            acceptance = data['acceptance']; basis = acceptance['basis']
            result['accepted'] = True
            result['acceptance'] = {'request_id': acceptance['request_id'], 'basis_hash': basis['basis_hash'], 'memory_id': source['memory_id']}
            from .authored_workflow_runtime_v208 import business_binding
            from .workspace_api import saved_execution
            _, release, business = saved_execution(db, p, task.id); adopted = business_binding(release, business)
            labels = {item['node_id']: item['name'] for item in adopted['step_mapping']}
            nodes = {node['id']: node for node in graph['nodes']}
            result['human_results'] = [{'activation_id': activation['id'], 'step_name': labels[activation['node_id']],
                'result': accepted.human_text(activation['output']), 'actor': accepted.person(db, nodes[activation['node_id']]['actor_id'])}
                for activation in task.data['graph_runtime']['activations'].values() if nodes[activation['node_id']]['kind'] == 'human'
                and activation['status'] == 'completed']
            proof['accepted_source_fingerprint'] = source['source_fingerprint']
            proof['acceptance'] = result['acceptance']; proof['accepted_proof_hash'] = digest(accepted_proof)
            result['reason'] = '原目标任务已独立验收，可由有资格的独立账号评价实际参考作用；不证明因果提升'
            try:
                feedback_allowed(db, p, row, task, managed, spec)
                result['permissions']['feedback'] = True; result['blocked_reasons'] = {}
            except HTTPException as error: result['blocked_reasons'] = {'feedback': str(error.detail)}
        result['consumption_fingerprint'] = digest(proof)
        return result


def view(db, p, row, *, transient=False):
    if not transient: origin.checked_use(db, p, row)
    binding = row.data['binding']; version = scoped(db, native.Version, row.document_version_id, p)
    progress = native_reference(db, p, row)
    labels = {'queued': '原件已保存，等待本地解析', 'parsing': '正在本地解析参考原文',
        'parse_failed': '解析未完成，原件已保留', 'awaiting_ack': '等待独立数据收档',
        'ready': '完整参考原文可加入本任务资料选择', 'withdrawn': '已撤回新的参考采用'}
    code = 'withdrawn' if row.status == 'withdrawn' else progress['code']
    permissions = {'stage_sources': False, 'withdraw': False, 'feedback': False}; reasons = {}
    target_task = scoped(db, m.Task, row.target_task_id, p)
    try:
        owner_gate(db, p, target_task, withdrawal=True)
        need(row.status == 'active', '此参考已经撤回，历史原件保留', 403)
        permissions['withdraw'] = True
    except HTTPException as error: reasons['withdraw'] = str(error.detail)
    try:
        owner_gate(db, p, target_task)
        need(row.status == 'active', '此参考已经撤回，历史原件保留', 403)
        need(progress['ready'], progress['reason'] or '真实解析和独立收档尚未完成', 403)
        permissions['stage_sources'] = True
    except HTTPException as error:
        reasons['stage_sources'] = str(error.detail)
    if transient:
        reasons['feedback'] = '请重新读取原目标任务的真实消费和独立验收依据'
    else:
        consumed = consumption(db, p, row, verify=False)
        permissions['feedback'] = consumed['permissions']['feedback']
        reasons.update(consumed['blocked_reasons'])
    return {'schema': 'layered-case-use-view-v210', 'id': row.id, 'version': row.version, 'status': row.status,
        'created_at': accepted.utc(row.created_at), 'author': accepted.person(db, row.author_id),
        'target': copy.deepcopy(binding['target']), 'case': copy.deepcopy(binding['case']),
        'applicable_reason': binding['applicable_reason'], 'boundaries': binding['boundaries'],
        'document': {'document_id': version.document_id, 'version_id': version.id, 'evidence_id': version.evidence_id,
            'name': version.name, 'original_url': '/api/runtime-documents/versions/' + version.id + '/original',
            'status': version.status, 'parse_status': progress['parse_status'], 'ack_verified': progress['ack_verified'], 'error': progress['error']},
        'stage': {'code': code, 'label': labels[code], 'reason': progress['reason']},
        'selectors': progress['selectors'] if row.status == 'active' else [],
        'selector_previews': progress['selector_previews'] if row.status == 'active' else [], 'capacity': progress['capacity'],
        'permissions': permissions, 'blocked_reasons': reasons, 'feedback': saved_feedback(db, p, row)}


def summary(db, p, row):
    value = view(db, p, row)
    return {'id': row.id, 'version': row.version, 'status': row.status, 'case_id': row.source_case_id,
        'title': row.data['binding']['case']['value']['title'], 'created_at': accepted.utc(row.created_at), 'stage': value['stage']}


def context(db, p, task_id):
    require(p, 'admin', 'operator', 'viewer')
    reverse_inventory(db, p, task_id)
    task = scoped(db, m.Task, task_id, p); managed = accepted.a.managed(db, p, task)
    target = {'task_id': task.id, 'title': task.title, 'business_name': '',
        'instance_id': task.data.get('instance_id'), 'release_id': task.data.get('instance_release_id')}
    allowed, reason = False, '此任务没有明确运行资料图及层级审批登记'
    if managed is not None:
        try:
            target, _, _ = origin.target_pin(db, p, task)
        except HTTPException as error:
            # An unsupported family is an explicit UI boundary. A damaged
            # frozen target is an integrity failure, not lack of permission.
            if error.status_code != 422: raise
            reason = str(error.detail)
        else:
            try:
                owner_gate(db, p, task); allowed, reason = True, None
            except HTTPException as error:
                # Paused/current-release/role/policy eligibility must not hide
                # historical references or their independent withdrawal path.
                reason = str(error.detail)
    page = list_uses(db, p, task_id=task_id, offset=0)
    return {'schema': 'layered-case-use-context-v210', 'task_id': task.id, 'task_version': task.version,
        'approval_version': managed.version if managed else None, 'target': target,
        'can_create': allowed, 'reason': reason, 'uses': page['records'], 'offset': 0, 'has_more': page['has_more']}


def list_uses(db, p, *, task_id=None, case_id=None, offset=0):
    require(p, 'admin', 'operator', 'viewer'); reverse_inventory(db, p, task_id)
    query = select(Use).where(Use.workspace_id == p.workspace_id)
    if task_id is not None: scoped(db, m.Task, task_id, p); query = query.where(Use.target_task_id == task_id)
    if case_id is not None: scoped(db, cases.Case, case_id, p); query = query.where(Use.source_case_id == case_id)
    rows = list(db.scalars(query.order_by(Use.created_at.desc(), Use.id.desc()).offset(offset).limit(21)))
    return {'records': [summary(db, p, row) for row in rows[:20]], 'offset': offset, 'has_more': len(rows) > 20}


def approved_cases(db, p, task_id, q, offset):
    require(p, 'admin', 'operator', 'viewer'); scoped(db, m.Task, task_id, p)
    page = cases.search(db, p, q, offset)
    for item in page['records']: item['source_fingerprint'] = scoped(db, cases.Case, item['id'], p).source_fingerprint
    return page


def reference_case(db, p, task_id, case_id):
    require(p, 'admin', 'operator', 'viewer'); scoped(db, m.Task, task_id, p)
    with origin.verification_scope(db): return origin.approved_case(db, p, case_id)[2]


def record(db, p, row, body, raw, sha, operation, path, previous=None, feedback_id=None):
    command = Command(id=m.uid(), workspace_id=p.workspace_id, use_id=row.id, actor_id=p.user_id,
        request_id=body.request_id, operation=operation, path=path, exact_body=raw, body_sha256=sha, version=1)
    data = checked(row); data['command_ids'].append(command.id); seal(row, data)
    authority = state(row)
    result = {'schema': 'layered-case-use-command-result-v210', 'request_id': body.request_id,
        'operation': operation, 'use_id': row.id, 'use_version': row.version, 'status': row.status,
        'feedback_id': feedback_id, 'use': view(db, p, row, transient=True)}
    seal(command, {'schema': 'layered-case-use-command-v210', 'result': result, 'result_hash': digest(result),
        'state': authority, 'state_hash': digest(authority), 'previous_state_hash': digest(previous) if previous else None})
    db.add(command); db.flush()
    db.add(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=COMMAND_AUDIT,
        object_id=command.id, data=command_proof(command))); db.flush()
    return copy.deepcopy(result)


def upload_nonce(p, request_id):
    return str(uuid5(UUID('7e425625-5261-4167-bcc5-5b048d38a6e9'), p.workspace_id + ':' + request_id))


def create(db, p, task_id, raw_body):
    body, raw, sha = parse(raw_body, 'create'); path = PREFIX + '/tasks/' + task_id + '/uses'
    lock_nonce(db, p, body.request_id)
    old = prior(db, p, body.request_id, raw=raw, sha=sha, path=path)
    if old is not None: return old
    require(p, 'admin', 'operator')
    from .signal_dual_handoffs import prelock_task
    prelock_task(db, p, task_id); task = scoped(db, m.Task, task_id, p, True)
    managed = accepted.a.managed(db, p, task, lock=True); need(managed is not None)
    # Task -> Managed -> exclusive real actor -> session: no shared User lock
    # upgrade before the real upload service takes its own exclusive actor lock.
    native.current_actor(db, p); lock_current_actor(db, p)
    owner_gate(db, p, task, lock=True); reverse_inventory(db, p, task.id)
    preview, target_pin, source_pin = selection(db, p, task.id, body)
    need(preview['can_create'], preview['reason'] or '当前不能生成参考资料')
    need(preview['preview_hash'] == body.preview_hash, '原预览、完整内容或容量已变化，请保留理由并重新核对')
    content = preview['reference_text'].encode('utf-8'); identity = m.uid()
    upload_body = {'request_id': upload_nonce(p, body.request_id), 'release_id': task.data['instance_release_id'],
        'title': '本任务已批准案例参考', 'file_name': file_name(preview['case']), 'file_size': len(content),
        'file_sha256': hashlib.sha256(content).hexdigest()}
    # A prior ordinary upload is not this family's authority to finish create.
    need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        or_((m.Audit.action == native.COMMAND) & (m.Audit.object_id == upload_body['request_id']),
            (m.Audit.action == origin.GENERATED) & (m.Audit.data['upload_request_id'].as_string() == upload_body['request_id'])))) is None
        and db.scalar(select(native.Version.id).where(native.Version.workspace_id == p.workspace_id,
            native.Version.data['origin']['request_hash'].as_string() == digest(upload_body))) is None,
        '派生原上传请求已有痕迹但本族成功链不存在；未借用上传结果补建采用')
    upload_result = native.upload(db, p, task.data['instance_id'], upload_body, content)
    version = scoped(db, native.Version, upload_result['latest_version_id'], p)
    document = {'document_id': version.document_id, 'version_id': version.id, 'evidence_id': version.evidence_id,
        'job_id': version.data['origin']['job_id']}
    binding = {'schema': 'layered-case-use-binding-v210', 'use_id': identity, 'author_id': p.user_id,
        'target': preview['target'], 'target_pin': target_pin, 'target_task_version': task.version,
        'target_approval_version': managed.version, 'source_pin': source_pin, 'case': preview['case'],
        'applicable_reason': body.applicable_reason, 'boundaries': preview['boundaries'], 'capacity': preview['capacity'],
        'document': document, 'upload_body': upload_body, 'upload_result': upload_result, 'preview_hash': body.preview_hash}
    row = Use(id=identity, workspace_id=p.workspace_id, target_task_id=task.id, source_case_id=body.case_id,
        author_id=p.user_id, document_version_id=version.id, binding_hash=digest(binding),
        creation_request_id=body.request_id, status='active', version=1, created_at=m.now())
    seal(row, {'schema': 'layered-case-use-state-v210', 'status': 'active', 'binding_hash': row.binding_hash,
        'binding': binding, 'command_ids': [], 'feedback_id': None, 'withdrawal': None})
    db.add(row); db.flush()
    marker = origin.marker(row); evidence = scoped(db, m.Evidence, version.evidence_id, p)
    version.data = {**version.data, origin.KEY: marker}; evidence.data = {**evidence.data, origin.KEY: marker}
    db.add(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=origin.GENERATED,
        object_id=row.id, data=origin.generation_proof(row))); db.flush()
    return record(db, p, row, body, raw, sha, 'create', path)


def withdraw(db, p, use_id, raw_body):
    body, raw, sha = parse(raw_body, 'withdraw'); path = PREFIX + '/uses/' + use_id + '/withdraw'
    lock_nonce(db, p, body.request_id); old = prior(db, p, body.request_id, raw=raw, sha=sha, path=path)
    if old is not None: return old
    row = scoped(db, Use, use_id, p)
    from .signal_dual_handoffs import prelock_task
    prelock_task(db, p, row.target_task_id); task = scoped(db, m.Task, row.target_task_id, p, True)
    accepted.a.managed(db, p, task, lock=True); lock_current_actor(db, p)
    row = scoped(db, Use, use_id, p, True); origin.checked_use(db, p, row)
    check_version(row, body.expected_use_version); owner_gate(db, p, task, lock=True, withdrawal=True)
    need(row.author_id == p.user_id and row.status == 'active', '只能由原采用人撤回当前未启动的参考', 403)
    previous = state(row); data = checked(row); row.status = 'withdrawn'; row.version += 1
    data['status'] = row.status; data['withdrawal'] = {'request_id': body.request_id, 'reason': body.reason,
        'actor_id': p.user_id, 'at': accepted.utc(m.now())}; seal(row, data); db.flush()
    return record(db, p, row, body, raw, sha, 'withdraw', path, previous)


def feedback(db, p, use_id, raw_body):
    body, raw, sha = parse(raw_body, 'feedback'); path = PREFIX + '/uses/' + use_id + '/feedback'
    lock_nonce(db, p, body.request_id); old = prior(db, p, body.request_id, raw=raw, sha=sha, path=path)
    if old is not None: return old
    row = scoped(db, Use, use_id, p)
    from .signal_dual_handoffs import prelock_task
    prelock_task(db, p, row.target_task_id); task = scoped(db, m.Task, row.target_task_id, p, True)
    managed = accepted.a.managed(db, p, task, lock=True); need(managed is not None)
    lock_current_actor(db, p); row = scoped(db, Use, use_id, p, True)
    origin.checked_use(db, p, row); check_version(row, body.expected_use_version)
    consumed = consumption(db, p, row, verify=False)
    need(consumed['consumed'] and consumed['accepted'], '目标任务尚未实际消费并独立验收，不能填写效果反馈', 403)
    need(consumed['consumption_fingerprint'] == body.consumption_fingerprint
        and consumed['acceptance']['request_id'] == body.acceptance_request_id
        and consumed['acceptance']['basis_hash'] == body.basis_hash,
        '本次反馈引用的消费或独立验收依据已变化，请重新核对')
    valid = {item['activation_id'] for item in consumed['human_results']}
    need(set(body.human_activation_ids) <= valid, '请选择本目标任务的真实人工结果，不接受其他任务或模型提案', 422)
    _, _, spec, _, _ = accepted.accepted_source(db, p, task.id)
    feedback_allowed(db, p, row, task, managed, spec, lock=True)
    previous = state(row)
    item = Feedback(id=m.uid(), workspace_id=p.workspace_id, use_id=row.id, actor_id=p.user_id,
        request_id=body.request_id, acceptance_request_id=body.acceptance_request_id,
        consumption_fingerprint=body.consumption_fingerprint, created_at=m.now(), version=1,
        data={'schema': 'layered-case-use-feedback-v210', 'target_task_id': task.id, 'value': body.model_dump(),
            'exact_body': raw, 'consumption': copy.deepcopy(consumed),
            'human_results': [entry for entry in consumed['human_results'] if entry['activation_id'] in body.human_activation_ids]})
    item.feedback_hash = digest(feedback_proof(item)); db.add(item)
    db.add(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=FEEDBACK_AUDIT,
        object_id=item.id, data={'use_id': row.id, 'feedback_id': item.id, 'request_id': body.request_id,
            'target_task_id': task.id, 'feedback_hash': item.feedback_hash}))
    data = checked(row); data['feedback_id'] = item.id; row.version += 1; seal(row, data); db.flush()
    return record(db, p, row, body, raw, sha, 'feedback', path, previous, item.id)


def recover(db, p, request_id, sha):
    require(p, 'admin', 'operator', 'viewer')
    result = prior(db, p, request_id, sha=sha)
    return {'schema': 'layered-case-use-request-recovery-v210', 'found': result is not None,
        'request_id': request_id, 'body_sha256': sha, 'result': result}


def get_use(db, p, identity):
    require(p, 'admin', 'operator', 'viewer'); reverse_inventory(db, p)
    return view(db, p, scoped(db, Use, identity, p))


def get_consumption(db, p, identity):
    require(p, 'admin', 'operator', 'viewer'); reverse_inventory(db, p)
    return consumption(db, p, scoped(db, Use, identity, p))


def list_feedback(db, p, case_id, offset):
    require(p, 'admin', 'operator', 'viewer'); scoped(db, cases.Case, case_id, p); reverse_inventory(db, p)
    query = select(Feedback).join(Use, Use.id == Feedback.use_id).where(Feedback.workspace_id == p.workspace_id,
        Use.workspace_id == p.workspace_id, Use.source_case_id == case_id).order_by(Feedback.created_at.desc(), Feedback.id.desc())
    rows = list(db.scalars(query.offset(offset).limit(21)))
    values = []
    for row in rows[:20]:
        use = scoped(db, Use, row.use_id, p)
        origin.checked_use(db, p, use); checked_feedback(db, p, row)
        consumed = consumption(db, p, use, verify=False)
        need(consumed['accepted'] and consumed['consumption_fingerprint'] == row.consumption_fingerprint,
            '反馈原目标消费或独立验收依据无法核对；未借用其他结果')
        values.append(feedback_view(db, row))
    return {'records': values, 'offset': offset, 'has_more': len(rows) > 20}
