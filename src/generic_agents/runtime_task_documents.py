"""Explicit received excerpts for one pre-submit Task; never a fact or grant."""
import copy
import hashlib
import json
from datetime import timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import func, select, text

from . import models as m, runtime_documents as native, runtime_document_ocr as ocr
from . import layered_approval_store_v175 as approval
from .contracts import Contract, digest
from .services import scoped, audit, check_version
from .typed_payload_codec_v131 import canonical_json
from .runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet as SourceSet
from .runtime_task_document_models_v207 import RuntimeTaskDocumentSourceCommand as SourceCommand

ANNOTATION = 'x-runtime-document-source-set-v207'
COMMAND = 'runtime_task_document.source_command_v207'
CREATED = 'runtime_task_document.source_set_created_v207'
MAX_TEXT = 20000
UUID_PATTERN = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
HASH_PATTERN = r'^[0-9a-f]{64}$'
ID = Annotated[str, Field(pattern=UUID_PATTERN)]
Hash = Annotated[str, Field(pattern=HASH_PATTERN)]
Positive = Annotated[StrictInt, Field(ge=1)]
same, anchored = native.same, native.anchored


def need(value, message='原任务资料、原引用或独立来源证明无法核对；未改用当前资料', code=409):
    if not value: raise HTTPException(code, message)


class Span(Contract):
    start: Annotated[StrictInt, Field(ge=0)]
    end: Annotated[StrictInt, Field(ge=1)]

    @model_validator(mode='after')
    def bounded(self):
        if not 0 < self.end - self.start <= 1200: raise ValueError('请选择1至1200个原文字符')
        return self


class NativeSelector(Contract):
    kind: Literal['native']
    version_id: ID
    attempt_id: ID
    parse_hash: Hash
    block_id: str = Field(pattern=r'^b[1-9][0-9]{0,4}$')
    char_span: Span


class OcrSelector(Contract):
    kind: Literal['ocr']
    version_id: ID
    run_id: ID
    page_id: ID
    result_id: ID
    result_hash: Hash
    region_id: str = Field(pattern=r'^r[1-9][0-9]{0,4}$')
    char_span: Span


Selector = Annotated[NativeSelector | OcrSelector, Field(discriminator='kind')]


class CreateSourceSet(Contract):
    request_id: ID
    expected_task_version: Positive
    expected_approval_version: Positive
    prior_set_id: ID | None
    prior_set_hash: Hash | None
    input_key: str = Field(min_length=1, max_length=200)
    selectors: list[Selector] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def parent(self):
        if (self.prior_set_id is None) != (self.prior_set_hash is None): raise ValueError('原来源集编号与摘要必须同时提供')
        return self


def ref_schema():
    return {'type': 'object', 'properties': {'source_set_id': {'type': 'string', 'pattern': UUID_PATTERN},
        'source_set_hash': {'type': 'string', 'pattern': HASH_PATTERN}},
        'required': ['source_set_id', 'source_set_hash'], 'additionalProperties': False, ANNOTATION: True}


def ports(graph):
    schema = graph['input_schema']; result = []
    for key, value in schema.get('properties', {}).items():
        if type(value) is not dict or ANNOTATION not in value: continue
        need(value[ANNOTATION] is True and same({k: v for k, v in value.items() if k not in {'title', 'description'}}, ref_schema()),
            '运行文档来源端口必须明确声明固定引用结构，不能接受任意JSON')
        result.append({'key': key, 'label': value.get('title') or '运行期资料依据', 'schema': copy.deepcopy(value)})
    return result


def creation_schema(schema):
    """Only an explicitly declared source input can be deferred until Task exists."""
    value = copy.deepcopy(schema); declared = {p['key'] for p in ports({'input_schema': schema})}
    if declared: value['required'] = [key for key in value.get('required', []) if key not in declared]
    return value


def verify_graph_sources(db, p, graph, *, current=True):
    """A declared port must really feed a pinned, registered packet producer."""
    descriptors = ports(graph)
    from . import workflow_document_packet_v207 as packet
    if not descriptors:
        need(not any(node.get('kind') == 'ability' and node.get('execution', {}).get('adapter') == packet.ADAPTER
            for node in graph.get('nodes', [])), '资料包生产节点缺少明确声明的来源端口')
        return {}
    qualified = {port['key']: [] for port in descriptors}
    for node in graph['nodes']:
        execution = node.get('execution', {})
        if node['kind'] != 'ability' or execution.get('adapter') != packet.ADAPTER: continue
        binding = execution.get('input_bindings', {}).get('source_set_ref')
        reference = graph.get('references', {}).get(binding)
        need(type(reference) is dict and reference.get('kind') == 'object' and reference.get('source') == 'input' and reference.get('node_id') is None
            and reference.get('field') in qualified and reference.get('nullable') is False
            and 'source_set_ref' not in execution.get('arguments', {}),
            '资料包节点须直接绑定此图明确声明的来源端口，不能使用常量或另一个输入')
        cap = scoped(db, m.Capability, execution['capability_id'], p)
        packet.validate_manifest(cap.data)
        need(cap.kind == 'tool' and cap.content_hash == cap.validated_hash == digest(cap.data) == execution['capability_hash']
            and cap.release_version == execution['release_version'] and execution.get('agent_role') == 'data'
            and type(execution.get('max_attempts')) is int and execution['max_attempts'] == 1
            and same(execution['input_schema'], packet.input_schema()) and same(execution['output_schema'], packet.output_schema())
            and (not current or cap.status == 'published'), '来源生产节点的真实登记、发布状态或固定合同无法核对')
        qualified[reference['field']].append({'node_id': node['id'], 'capability_id': cap.id,
            'capability_hash': cap.content_hash, 'actor_id': node['actor_id']})
    need(all(qualified.values()), '来源端口尚未接到已登记的真实资料包生产节点，不能只保存一个空标记')
    return qualified


def task_context(db, p, task):
    from .workspace_api import saved_execution
    from .workflow_graph import graph_for
    _, release, business = saved_execution(db, p, task.id)
    need(business.get('execution_mode') == 'confirmed_graph_workflow', '此业务没有已发布的运行文档来源端口；需新发布版与新任务')
    graph = graph_for(release, business)
    producer_pins = verify_graph_sources(db, p, graph, current=False)
    return release, graph, {'task_id': task.id, 'instance_id': task.data['instance_id'], 'release_id': release.id,
        'package_id': task.package_id, 'owner_id': task.owner_id, 'creation_hash': task.data.get('creation_hash'),
        'configuration_hash': release.configuration_hash, 'graph_hash': digest(graph), 'producer_pins': producer_pins}


def context(db, p, identity):
    task = scoped(db, m.Task, identity, p); managed = approval.managed(db, p, task)
    from .workspace_api import saved_execution
    from .workflow_graph import graph_for
    _, release, business = saved_execution(db, p, task.id)
    descriptors = ports(graph_for(release, business)) if business.get('execution_mode') == 'confirmed_graph_workflow' else []
    data = approval.checked(managed) if managed else None
    available = False; reason = '此任务不在可选择资料的层级审批草稿中'
    if descriptors and managed and p.user_id == task.owner_id and managed.status in {'draft', 'returned', 'withdrawn'} and data['execution'] is None:
        current = native.context(db, p, task.data['instance_id'])
        available = bool(current['can_upload'] and current['release_id'] == release.id)
        producer_issue = None
        try: verify_graph_sources(db, p, graph_for(release, business), current=True)
        except HTTPException as error: available = False; producer_issue = str(error.detail)
        if available: reason = None
        else: reason = producer_issue or current['reason'] or '实例发布版本已变化'
    elif not descriptors: reason = '此发布业务未声明运行文档来源端口；需明确配置新发布版与新任务'
    values = data['draft_value']['graph_inputs'] if data else {}
    heads = {}
    for port in descriptors:
        head = db.scalar(select(SourceSet).where(SourceSet.workspace_id == p.workspace_id,
            SourceSet.task_id == task.id, SourceSet.input_key == port['key']).order_by(SourceSet.ordinal.desc()).limit(1))
        heads[port['key']] = {'source_set_id': head.id, 'source_set_hash': head.source_set_hash} if head else None
    return {'schema': 'runtime-task-document-context-v207', 'task_id': task.id, 'instance_id': task.data['instance_id'],
        'release_id': release.id, 'task_version': task.version, 'approval_version': managed.version if managed else None,
        'approval_status': managed.status if managed else None, 'can_select': available, 'reason': reason,
        'input_ports': descriptors, 'source_set_heads': heads,
        'draft_source_refs': {port['key']: copy.deepcopy(values.get(port['key'])) for port in descriptors}}


def fingerprint(row):
    return digest({'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'action': row.action, 'object_id': row.object_id, 'data': row.data})


def ack_proof(db, p, event_id, action):
    event = scoped(db, m.Event, event_id, p)
    need(event.status == 'received' and event.received_at is not None, '原片段尚未获得可核对的独立收档回执')
    memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.data['event_id'].as_string() == event.id)))
    acknowledgments = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == event.id)))
    need(len(memories) == len(acknowledgments) == 1)
    return {'event_id': event.id, 'receipt_hash': digest(event.data['receipt']), 'memory_id': memories[0].id,
        'memory_hash': digest(memories[0].data), 'ack_audit_id': acknowledgments[0].id, 'ack_audit_hash': fingerprint(acknowledgments[0])}


def resolve(db, p, selector, instance_id, *, task=None):
    """No caller supplied text, latest-pointer fallback, or current uploader grant."""
    version = scoped(db, native.Version, selector['version_id'], p)
    need(version.instance_id == instance_id, '资料不属于本任务实例，未跨实例借用')
    if selector['kind'] == 'native':
        from . import runtime_document_receipts as delivery
        attempt, result = native.selected_attempt(db, p, version, selector['attempt_id'], selector['parse_hash'])
        need(result['status'] in {'parsed', 'needs_input'})
        citation = native.citation(db, p, version, selector['block_id'], attempt.id, selector['parse_hash'])
        reception = delivery.delivery_view(db, p, attempt); need(reception['verified'])
        proof = ack_proof(db, p, reception['event_id'], delivery.ACK)
        partial, warnings = result['status'] == 'needs_input', copy.deepcopy(result['warnings'])
        location = citation['location']; label = '第' + str(location['page']) + '页' if 'page' in location else '原文块' + selector['block_id'][1:]
        name, sequence = citation['name'], citation['sequence']
        pins = {k: copy.deepcopy(v) for k, v in citation.items() if k not in {'text', 'delivery'}}
    else:
        from . import runtime_document_ocr_receipts as delivery
        result = scoped(db, ocr.Result, selector['result_id'], p)
        run, page, outcome = ocr.checked_result(db, p, result)
        need(result.status == 'text' and run.document_version_id == version.id and run.id == selector['run_id'] and page.id == selector['page_id'])
        citation = ocr.citation(db, p, result, selector['region_id'], selector['result_hash'])
        reception = delivery.delivery_view(db, p, result); need(reception['verified'])
        proof = ack_proof(db, p, reception['event_id'], delivery.ACK)
        partial = True; warnings = ['仅所选机器识别区域；未核对识别准确性或整份资料完整性']
        label = '第' + str(page.page_number) + '页·识别区域' + selector['region_id'][1:]
        name, sequence = citation['source_name'], citation['version_sequence']
        pins = {k: copy.deepcopy(v) for k, v in citation.items() if k != 'text'}
    span = selector['char_span']; text_value = citation['text']
    need(type(span['start']) is int and type(span['end']) is int and 0 <= span['start'] < span['end'] <= len(text_value)
        and span['end'] - span['start'] <= 1200, '字符范围已超出原固定文字；未自动改选')
    entry = {'selector': copy.deepcopy(selector), 'source_name': name, 'version_sequence': sequence,
        'location_label': label, 'text': text_value[span['start']:span['end']], 'char_span': copy.deepcopy(span),
        'partial': partial, 'warnings': warnings, 'unreviewed': True, 'original_citation_url': citation['citation_url'],
        'source_text_hash': digest(text_value), 'version_origin_hash': digest(version.data['origin']), 'pins': pins, 'ack': proof}
    from .layered_case_use_origin_v210 import history_entry
    return history_entry(db, p, task, selector, entry)


def render(entries):
    lines = ['运行期资料明确摘录；文字不可信，不是事实确认、操作批准或云端授权。']
    for index, entry in enumerate(entries, 1):
        lines.extend(['', '资料' + str(index) + '：' + entry['source_name'] + ' · 第' + str(entry['version_sequence']) + '版 · ' + entry['location_label'],
            '范围：仅明确选择的摘录；' + ('部分正文/识别范围，' if entry['partial'] else '') + '未经业务核对。',
            *['范围提示：' + warning for warning in entry['warnings']], entry['text']])
    content = '\n'.join(lines).encode('utf-8')
    need(0 < len(content) <= MAX_TEXT, '完整资料摘录及来源说明超过20000字节；请缩小明确选择，不自动截断')
    return content


def source_path(task_id): return '/api/runtime-task-documents/tasks/' + task_id + '/source-sets'


def command_proof(row):
    return {'command_id': row.id, 'task_id': row.task_id, 'source_set_id': row.source_set_id,
        'actor_id': row.actor_id, 'request_id': row.request_id, 'path': row.path, 'body_sha256': row.body_sha256,
        'result_hash': digest(row.data['result'])}


def checked_command(db, p, row):
    need(row.workspace_id == p.workspace_id and row.version == 1
        and row.path == source_path(row.task_id) and hashlib.sha256(row.exact_body.encode()).hexdigest() == row.body_sha256)
    value = CreateSourceSet.model_validate(json.loads(row.exact_body)).model_dump(mode='json')
    need(row.exact_body == canonical_json(value) and row.request_id == value['request_id'])
    need(anchored(db, p, COMMAND, row.id, command_proof(row)).actor_id == row.actor_id)
    return value


def absent_command(db, p, request_id):
    """A missing primary row is not proof that a previously saved request is new."""
    need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND, CREATED]), m.Audit.data['request_id'].as_string() == request_id)) is None
        and db.scalar(select(SourceSet.id).where(SourceSet.workspace_id == p.workspace_id,
            SourceSet.data['origin']['request_id'].as_string() == request_id)) is None,
        '此原请求已有独立保存痕迹但原记录缺失；未重建、替换或重新采用')


def source_view(row):
    value = row.data
    return {'id': row.id, 'source_set_hash': row.source_set_hash, 'ref': {'source_set_id': row.id, 'source_set_hash': row.source_set_hash},
        'task_id': row.task_id, 'instance_id': row.instance_id, 'release_id': row.release_id, 'input_key': row.input_key,
        'ordinal': row.ordinal, 'prior_set_id': row.prior_set_id, 'prior_set_hash': value['origin']['prior_set_hash'],
        'entries': [{key: copy.deepcopy(item[key]) for key in ('id', 'kind', 'selector', 'source_name', 'version_sequence',
            'location_label', 'text', 'char_span', 'partial', 'warnings', 'unreviewed', 'original_citation_url')} for item in value['entries']],
        'rendered_bytes': value['rendered_bytes'], 'created_at': row.created_at.astimezone(timezone.utc).isoformat()}


def checked_set(db, p, row, *, task=None, input_key=None):
    task = task or scoped(db, m.Task, row.task_id, p)
    release, graph, current = task_context(db, p, task); origin = row.data['origin']
    need(row.version == 1 and row.task_id == task.id and row.instance_id == current['instance_id']
        and row.release_id == release.id and row.actor_id == task.owner_id
        and same(origin['task_context'], current) and row.source_set_hash == digest(row.data)
        and (input_key is None or row.input_key == input_key))
    descriptor = next((item for item in ports(graph) if item['key'] == row.input_key), None)
    need(descriptor is not None and digest(descriptor['schema']) == origin['port_schema_hash'])
    command = scoped(db, SourceCommand, origin['command_id'], p); body = checked_command(db, p, command)
    need(command.source_set_id == row.id and command.task_id == task.id and command.actor_id == row.actor_id
        and body['input_key'] == row.input_key and body['prior_set_id'] == row.prior_set_id
        and body['prior_set_hash'] == origin['prior_set_hash'] and body['selectors'] == origin['selectors']
        and command.data['result']['id'] == row.id and command.data['result']['source_set_hash'] == row.source_set_hash)
    need(same(command.data['result'], source_view(row)))
    need(anchored(db, p, CREATED, row.id, {'source_set_hash': row.source_set_hash, 'command_id': command.id, 'request_id': body['request_id'],
        'task_context_hash': digest(current)}).actor_id == row.actor_id)
    from .layered_case_use_origin_v210 import verification_scope, checked_coverage
    with native.verification_scope(db), verification_scope(db):
        entries = [{'id': 's' + str(i), 'kind': selector['kind'], **resolve(db, p, selector, row.instance_id, task=task)}
            for i, selector in enumerate(body['selectors'], 1)]
        checked_coverage(db, p, task, entries)
    need(same(entries, row.data['entries']) and len(render(entries)) == row.data['rendered_bytes'])
    return row


def validate_inputs(db, p, task, graph, values, *, allow_deferred=False):
    descriptors = ports(graph)
    verify_graph_sources(db, p, graph, current=False)
    for port in descriptors:
        if port['key'] not in values and allow_deferred: continue
        need(port['key'] in values, '请先明确选择运行期资料依据并用于本任务，再提交审批', 422)
        value = values[port['key']]
        need(type(value) is dict and set(value) == {'source_set_id', 'source_set_hash'})
        row = scoped(db, SourceSet, value['source_set_id'], p)
        need(row.source_set_hash == value['source_set_hash'])
        checked_set(db, p, row, task=task, input_key=port['key'])
    return bool(descriptors)


def validate_edit_scope(db, p, task, graph):
    """Live edit/submit only; historical references and command reads skip it."""
    if not ports(graph): return False
    native.current_actor(db, p)
    need(task.owner_id == p.user_id, '请由原任务发起人选择并提交运行期资料', 403)
    native.active(db, p, task.data['instance_id'], task.data['instance_release_id'])
    verify_graph_sources(db, p, graph, current=True)
    return True


def create_set(db, p, identity, body):
    task = scoped(db, m.Task, identity, p)
    exact = canonical_json(body); hashed = hashlib.sha256(exact.encode()).hexdigest()
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest([p.workspace_id, body['request_id']])[:15], 16)})
    prior = db.scalar(select(SourceCommand).where(SourceCommand.workspace_id == p.workspace_id, SourceCommand.request_id == body['request_id']))
    if prior:
        checked_command(db, p, prior)
        need(prior.actor_id == p.user_id and prior.task_id == identity and prior.exact_body == exact, '原来源请求不能更换账号、路径或正文')
        checked_set(db, p, scoped(db, SourceSet, prior.source_set_id, p), task=task)
        return copy.deepcopy(prior.data['result'])
    absent_command(db, p, body['request_id'])
    from .signal_dual_handoffs import prelock_task
    prelock_task(db, p, identity); task = scoped(db, m.Task, identity, p, True)
    managed = approval.managed(db, p, task, lock=True); need(managed is not None)
    check_version(task, body['expected_task_version']); check_version(managed, body['expected_approval_version'])
    native.current_actor(db, p)
    need(task.owner_id == p.user_id and managed.status in {'draft', 'returned', 'withdrawn'}
        and approval.checked(managed)['execution'] is None, '仅原发起人在未启动的审批草稿中选择资料', 403)
    release, graph, frozen = task_context(db, p, task)
    validate_edit_scope(db, p, task, graph)
    descriptor = next((item for item in ports(graph) if item['key'] == body['input_key']), None)
    need(descriptor is not None, '此发布图没有相应运行文档来源端口，不能任意注入字段', 422)
    head = db.scalar(select(SourceSet).where(SourceSet.workspace_id == p.workspace_id, SourceSet.task_id == task.id,
        SourceSet.input_key == body['input_key']).order_by(SourceSet.ordinal.desc()).limit(1))
    need((head.id if head else None) == body['prior_set_id'] and (head.source_set_hash if head else None) == body['prior_set_hash'],
        '来源草稿已有新修订；请保留本次选择后核对，不自动覆盖')
    command_id = str(uuid4())
    from .layered_case_use_origin_v210 import verification_scope, checked_coverage, current_entries
    with native.verification_scope(db), verification_scope(db):
        entries = [{'id': 's' + str(index), 'kind': selector['kind'], **resolve(db, p, selector, frozen['instance_id'], task=task)}
            for index, selector in enumerate(body['selectors'], 1)]
        checked_coverage(db, p, task, entries)
        current_entries(db, p, task, entries)
    content = render(entries)
    origin = {'task_context': frozen, 'request_id': body['request_id'], 'port_schema_hash': digest(descriptor['schema']), 'selectors': copy.deepcopy(body['selectors']),
        'command_id': command_id, 'prior_set_hash': body['prior_set_hash']}
    value = {'origin': origin, 'entries': entries, 'rendered_bytes': len(content)}
    row = SourceSet(workspace_id=p.workspace_id, task_id=task.id, instance_id=frozen['instance_id'], release_id=release.id,
        actor_id=p.user_id, input_key=body['input_key'], ordinal=(head.ordinal + 1 if head else 1), prior_set_id=body['prior_set_id'],
        source_set_hash=digest(value), data=value)
    db.add(row); db.flush(); result = source_view(row)
    command = SourceCommand(id=command_id, workspace_id=p.workspace_id, task_id=task.id, source_set_id=row.id,
        actor_id=p.user_id, request_id=body['request_id'], path=source_path(task.id), exact_body=exact, body_sha256=hashed,
        data={'result': copy.deepcopy(result)})
    db.add(command); db.flush(); audit(db, p, COMMAND, command.id, command_proof(command))
    audit(db, p, CREATED, row.id, {'source_set_hash': row.source_set_hash, 'command_id': command.id,
        'request_id': body['request_id'], 'task_context_hash': digest(frozen)})
    return result


def recover(db, p, identity, expected_hash):
    row = db.scalar(select(SourceCommand).where(SourceCommand.workspace_id == p.workspace_id, SourceCommand.request_id == identity))
    if row is None:
        absent_command(db, p, identity)
        return {'schema': 'runtime-task-document-command-v207', 'found': False, 'body': None, 'result': None}
    body = checked_command(db, p, row)
    need(row.actor_id == p.user_id and row.body_sha256 == expected_hash, '原来源请求账号或正文摘要不一致')
    scoped(db, m.Task, row.task_id, p)
    checked_set(db, p, scoped(db, SourceSet, row.source_set_id, p))
    return {'schema': 'runtime-task-document-command-v207', 'found': True, 'body': body, 'result': copy.deepcopy(row.data['result'])}
