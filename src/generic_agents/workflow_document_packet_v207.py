"""Registered local packet from approved, immutable Task document excerpts."""
import copy
import hashlib
import json
from datetime import timezone
from sqlalchemy import select, or_, func
from fastapi import HTTPException

from . import models as m, runtime_task_documents as sources
from .contracts import Manifest, digest
from .services import scoped, audit, published, require, validate
from .evidence import save_evidence, evidence_bytes
from .typed_payload_codec_v131 import canonical_json
from .runtime_task_document_models_v207 import WorkflowDocumentPacketV207 as Packet
from .runtime_task_document_models_v207 import WorkflowDocumentPacketCommandV207 as Command

ADAPTER = 'runtime_document_packet'
APPROVAL = 'workflow-document-packet-approval-v207'
RECEIPT = 'workflow-document-packet-receipt-v207'
CONTEXT = 'workflow-document-packet-context-v207'
KEY = 'workflow_document_packet_v207'
SAVED = 'workflow.document_packet.saved_v207'
PRODUCED = 'workflow.document_packet.produced_v207'
need, same = sources.need, sources.same


class ProducerPending(Exception):
    def __init__(self, job_id, invocation_id):
        self.job_id, self.invocation_id = job_id, invocation_id
        super().__init__('原资料包已提交，等待同一生产任务完成')


def obj(properties): return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
def ident(): return {'type': 'string', 'pattern': sources.UUID_PATTERN}
def hashed(): return {'type': 'string', 'pattern': sources.HASH_PATTERN}


def input_schema():
    ref = sources.ref_schema(); ref.pop(sources.ANNOTATION)
    return obj({'source_set_ref': ref})


def output_schema():
    return obj({'schema': {'const': RECEIPT}, 'evidence_ids': {'type': 'array', 'items': ident(), 'minItems': 1, 'maxItems': 1},
        'packet': obj({'id': ident(), 'evidence_id': ident(), 'sha256': hashed(), 'version': {'const': 1},
            'source_set_id': ident(), 'source_set_hash': hashed()}),
        'context_result': obj({'schema': {'const': CONTEXT}, 'task_id': ident(), 'source_set_ref': input_schema()['properties']['source_set_ref'],
            'source_count': {'type': 'integer', 'minimum': 1, 'maximum': 20}, 'selected_text_only': {'const': True}, 'unreviewed': {'const': True}}),
        'proposal_only': {'const': True}, 'cloud_authorized': {'const': False}, 'physical_state_verified': {'const': False}})


def _inputs(value): validate(input_schema(), value); return copy.deepcopy(value)


def validate_manifest(manifest):
    data = manifest.model_dump(mode='json') if isinstance(manifest, Manifest) else copy.deepcopy(manifest)
    Manifest.model_validate(data)
    need(data['kind'] == 'tool' and data['allowed_agent_roles'] == ['data']
        and same(data['execution'], {'adapter': ADAPTER, 'side_effect': True})
        and same(data['input_schema'], input_schema()) and same(data['output_schema'], output_schema())
        and data.get('dependencies') == [] and data['data_policy'].get('cloud_allowed') is False
        and type(data['budget'].get('max_attempts')) is int and data['budget']['max_attempts'] == 1
        and same(data.get('specification'), {'schema': 'workflow-document-packet-contract-v207', 'operation': 'build_approved_document_packet'}),
        '运行文档证据包须独立登记为本地数据能力，固定完整输入/输出且不授云许可', 422)
    return data


def make_approval(task, job, node, inputs):
    return {'schema': APPROVAL, 'task_id': task.id, 'job_id': job.id, 'activation_id': job.data['activation_id'],
        'graph_hash': job.data['graph_hash'], 'cancel_epoch': job.data['cancel_epoch'], 'input_hash': digest(inputs),
        'capability_id': node['execution']['capability_id'], 'capability_hash': node['execution']['capability_hash']}


def approved_set(db, p, task, reference):
    row = scoped(db, sources.SourceSet, reference['source_set_id'], p)
    need(row.source_set_hash == reference['source_set_hash'])
    sources.checked_set(db, p, row, task=task)
    managed = sources.approval.managed(db, p, task); need(managed is not None)
    submission, value, _ = sources.approval.current_submission(db, p, managed)
    need(same(value['value']['graph_inputs'].get(row.input_key), reference), '此资料集不是当前任务批准的原来源')
    return row, {'submission_id': submission.id, 'submission_hash': submission.data['record_hash'],
        'source_set_id': row.id, 'source_set_hash': row.source_set_hash, 'input_key': row.input_key}


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    from . import workflow_graph_scope as scope
    from . import workflow_graph
    from .leases import ownership
    require(p, 'admin', 'operator'); _inputs(inputs); validate_manifest(cap.data)
    need(type(approval) is dict and approval.get('schema') == APPROVAL)
    lease = ownership(db)
    job = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.id == approval.get('job_id'),
        m.Job.kind == scope.KIND, m.Job.status == 'running', m.Job.lease_token == lease.get('lease_token'),
        m.Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(job is not None and lease.get('job_id') == job.id and bool(lease.get('lease_token'))
        and job.data.get('actor_id') == p.user_id and job.data.get('invocation_key') == key
        and job.data.get('task_id') == approval.get('task_id'))
    task = scoped(db, m.Task, approval['task_id'], p, True)
    task, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, approval['activation_id'])
    transport = scope._job_matches(task, job, state, activation, node)
    actual = published(db, p, cap.id, 'tool')
    from .full_semantic_graph_v1 import ability_inputs
    need(role == 'data' and node['execution']['agent_role'] == role and node['execution']['adapter'] == ADAPTER
        and node['execution']['capability_id'] == actual.id and node['execution']['capability_hash'] == actual.content_hash == cap.content_hash
        and same(node['execution']['input_schema'], input_schema()) and same(node['execution']['output_schema'], output_schema())
        and node['actor_id'] == p.user_id and state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids'] and activation['epoch'] == state['cancel_epoch']
        and transport.get('phase') == 'running' and same(approval, make_approval(task, job, node, inputs))
        and same(inputs, job.data['inputs']) and same(inputs, ability_inputs(graph, state, activation['id'])))
    sources.approval.guard(db, p, task, record_actor=True)
    workflow_graph.live(db, p, task, release, business)
    source_set, selected = approved_set(db, p, task, inputs['source_set_ref'])
    need(same(task.data['graph_runtime']['inputs'].get(source_set.input_key), inputs['source_set_ref']))
    return {'task': task, 'job': job, 'node': node, 'scope': scope._scope(task, job, node, activation), 'source_set': source_set, 'selected': selected}


def marker(packet, command):
    return {'schema': KEY, 'packet_id': packet.id, 'command_id': command.id, 'task_id': packet.task_id,
        'source_set_id': packet.source_set_id, 'source_set_hash': packet.data['selected']['source_set_hash'],
        'request_key_hash': digest(packet.request_key)}


def packet_proof(row):
    return {'id': row.id, 'workspace_id': row.workspace_id, 'task_id': row.task_id, 'source_set_id': row.source_set_id,
        'actor_id': row.actor_id, 'invocation_id': row.invocation_id, 'job_id': row.job_id, 'evidence_id': row.evidence_id,
        'request_key': row.request_key, 'sha256': row.sha256, 'data': copy.deepcopy(row.data)}


def command_proof(row):
    return {'id': row.id, 'packet_id': row.packet_id, 'task_id': row.task_id, 'invocation_id': row.invocation_id,
        'actor_id': row.actor_id, 'request_key': row.request_key, 'body_sha256': row.body_sha256, 'data': copy.deepcopy(row.data)}


def _negative(db, p, call):
    need(KEY not in call.data and db.scalar(select(Command.id).where(Command.workspace_id == p.workspace_id,
        or_(Command.request_key == call.idempotency_key, Command.invocation_id == call.id))) is None
        and db.scalar(select(Packet.id).where(Packet.workspace_id == p.workspace_id,
            or_(Packet.request_key == call.idempotency_key, Packet.invocation_id == call.id))) is None
        and db.scalar(select(m.Evidence.id).where(m.Evidence.workspace_id == p.workspace_id,
            m.Evidence.data[KEY]['request_key_hash'].as_string() == digest(call.idempotency_key))) is None
        and db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action.in_([SAVED, PRODUCED]), m.Audit.data['invocation_id'].as_string() == call.id)) is None,
        '原资料包存在保存痕迹但库存不完整；未沿旧成功调用重新生成')


def execute(db, p, cap, inputs, key, approval):
    current = validate_call_approval(db, p, cap, 'data', inputs, key, approval)
    call = db.scalar(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
        m.Invocation.idempotency_key == key).execution_options(populate_existing=True)); need(call is not None)
    if KEY in call.data or db.scalar(select(Command.id).where(Command.workspace_id == p.workspace_id, Command.request_key == key)):
        return reconcile(db, p, cap, inputs, key, approval, call)
    _negative(db, p, call)
    need(call.status == 'running' and call.data.get('approval') == approval and call.data.get('side_effect') is True)
    source_set = current['source_set']; content = sources.render(source_set.data['entries'])
    evidence = save_evidence(db, p, content, 'text/plain'); actual, raw = evidence_bytes(db, p, evidence.id)
    need(raw == content and actual.sha256 == hashlib.sha256(content).hexdigest() and actual.data.get('cloud_model_ids') == [])
    packet = Packet(workspace_id=p.workspace_id, task_id=current['task'].id, source_set_id=source_set.id,
        actor_id=p.user_id, invocation_id=call.id, job_id=current['job'].id, evidence_id=evidence.id,
        request_key=key, sha256=evidence.sha256, data={'selected': current['selected'], 'scope': current['scope'],
            'source_snapshot_hash': digest(source_set.data), 'source_input': copy.deepcopy(inputs),
            'approval': copy.deepcopy(approval), 'evidence_at_creation': {'id': evidence.id, 'sha256': evidence.sha256,
                'size_bytes': len(content), 'media_type': 'text/plain', 'uploaded_by': p.user_id, 'cloud_model_ids': []}})
    db.add(packet); db.flush()
    output = {'schema': RECEIPT, 'evidence_ids': [evidence.id], 'packet': {'id': packet.id, 'evidence_id': evidence.id,
        'sha256': evidence.sha256, 'version': 1, 'source_set_id': source_set.id, 'source_set_hash': source_set.source_set_hash},
        'context_result': {'schema': CONTEXT, 'task_id': packet.task_id, 'source_set_ref': copy.deepcopy(inputs['source_set_ref']),
            'source_count': len(source_set.data['entries']), 'selected_text_only': True, 'unreviewed': True},
        'proposal_only': True, 'cloud_authorized': False, 'physical_state_verified': False}
    validate(output_schema(), output)
    body = {'schema': KEY + '/command', 'request_key': key, 'task_id': packet.task_id, 'invocation_id': call.id,
        'job_id': packet.job_id, 'approval': copy.deepcopy(approval), 'inputs': copy.deepcopy(inputs)}
    exact = canonical_json(body)
    command = Command(workspace_id=p.workspace_id, packet_id=packet.id, task_id=packet.task_id, actor_id=p.user_id,
        invocation_id=call.id, request_key=key, exact_body=exact, body_sha256=hashlib.sha256(exact.encode()).hexdigest(),
        data={'output': copy.deepcopy(output), 'packet_hash': digest(packet_proof(packet))})
    db.add(command); db.flush(); audit(db, p, SAVED, command.id, command_proof(command))
    pin = marker(packet, command); call.data = {**call.data, KEY: pin}; evidence.data = {**evidence.data, KEY: pin}
    return output


def receipt_audit_data(call, job, output):
    validate(output_schema(), output)
    need(call.status == 'succeeded' and job.kind == 'execute_full_semantic_graph_node'
        and call.data.get('job_id') == job.id and job.data.get('invocation_key') == call.idempotency_key
        and same(call.data.get('output'), output) and call.data.get('role') == 'data' and call.data.get('side_effect') is True)
    return {'schema': KEY + '/produced', 'invocation_id': call.id, 'job_id': job.id, 'task_id': job.data['task_id'],
        'capability_id': call.capability_id, 'capability_hash': call.data['capability_hash'], 'input_hash': call.data['input_hash'],
        'output_hash': digest(output), 'packet_id': output['packet']['id'], 'evidence_id': output['packet']['evidence_id'],
        'request_key_hash': digest(call.idempotency_key)}


def mark_produced(db, p, call, job, output):
    value = receipt_audit_data(call, job, output)
    old = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == PRODUCED, m.Audit.object_id == call.id)))
    if old: need(len(old) == 1 and old[0].actor_id == job.data['actor_id'] and same(old[0].data, value))
    else: audit(db, p, PRODUCED, call.id, value)


def _inventory(db, p, invocation_id):
    packets = list(db.scalars(select(Packet).where(Packet.workspace_id == p.workspace_id, Packet.invocation_id == invocation_id)))
    need(len(packets) == 1); packet = packets[0]
    task = scoped(db, m.Task, packet.task_id, p); source_set = scoped(db, sources.SourceSet, packet.source_set_id, p)
    sources.checked_set(db, p, source_set, task=task)
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        or_(Command.packet_id == packet.id, Command.request_key == packet.request_key, Command.invocation_id == invocation_id))))
    need(len(commands) == 1); command = commands[0]
    call = scoped(db, m.Invocation, invocation_id, p); job = scoped(db, m.Job, packet.job_id, p)
    need(packet.version == command.version == 1 and command.packet_id == packet.id and command.task_id == packet.task_id
        and command.invocation_id == call.id and command.actor_id == packet.actor_id and command.request_key == packet.request_key == call.idempotency_key
        and command.body_sha256 == hashlib.sha256(command.exact_body.encode()).hexdigest()
        and command.data['packet_hash'] == digest(packet_proof(packet))
        and same(call.data.get(KEY), marker(packet, command)) and call.status == 'succeeded'
        and call.data.get('job_id') == job.id and job.kind == 'execute_full_semantic_graph_node'
        and job.data.get('task_id') == packet.task_id and job.data.get('actor_id') == packet.actor_id
        and job.data.get('inputs') == packet.data['source_input'] and job.data.get('input_hash') == call.data.get('input_hash') == digest(packet.data['source_input'])
        and same(call.data.get('approval'), packet.data['approval']) and job.data.get('invocation_key') == packet.request_key)
    need(sources.anchored(db, p, SAVED, command.id, command_proof(command)).actor_id == packet.actor_id)
    expected_body = {'schema': KEY + '/command', 'request_key': packet.request_key, 'task_id': packet.task_id,
        'invocation_id': call.id, 'job_id': job.id, 'approval': packet.data['approval'], 'inputs': packet.data['source_input']}
    need(command.exact_body == canonical_json(expected_body))
    output = command.data['output']; validate(output_schema(), output)
    need(same(output, call.data['output']) and output['packet']['id'] == packet.id and output['packet']['evidence_id'] == packet.evidence_id
        and output['packet']['sha256'] == packet.sha256 and output['packet']['source_set_id'] == source_set.id
        and output['packet']['source_set_hash'] == source_set.source_set_hash
        and same(output['context_result']['source_set_ref'], packet.data['source_input']['source_set_ref'])
        and source_set.source_set_hash == packet.data['selected']['source_set_hash']
        and digest(source_set.data) == packet.data['source_snapshot_hash'])
    managed = sources.approval.managed(db, p, task); need(managed is not None)
    submission = scoped(db, sources.approval.Submission, packet.data['selected']['submission_id'], p)
    submitted = sources.approval.checked(submission)
    need(submission.managed_id == managed.id and submission.data['record_hash'] == packet.data['selected']['submission_hash']
        and same(submitted['value']['graph_inputs'].get(source_set.input_key), output['context_result']['source_set_ref']))
    from . import workflow_graph_scope as scope, workflow_graph
    _, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
    transport = scope._job_matches(task, job, state, activation, node)
    need(node['execution']['adapter'] == ADAPTER and node['execution']['agent_role'] == 'data' and node['actor_id'] == packet.actor_id
        and call.capability_id == node['execution']['capability_id'] and call.data['capability_hash'] == node['execution']['capability_hash']
        and same(packet.data['scope'], scope._scope(task, job, node, activation)))
    need(sources.anchored(db, p, PRODUCED, call.id, receipt_audit_data(call, job, output)).actor_id == packet.actor_id)
    actual, raw = evidence_bytes(db, p, packet.evidence_id)
    creation = packet.data['evidence_at_creation']
    need(same(actual.data.get(KEY), marker(packet, command)) and actual.sha256 == packet.sha256
        and actual.media_type == 'text/plain' and actual.data.get('uploaded_by') == packet.actor_id
        and raw == sources.render(source_set.data['entries']) and actual.size_bytes == len(raw)
        and same(creation, {'id': actual.id, 'sha256': actual.sha256, 'size_bytes': actual.size_bytes,
            'media_type': 'text/plain', 'uploaded_by': packet.actor_id, 'cloud_model_ids': []}))
    need(sources.anchored(db, p, 'evidence.upload', actual.id, {'media_type': 'text/plain', 'sha256': actual.sha256,
        'cloud_model_id': None, 'consent': False}).actor_id == packet.actor_id)
    return packet, command, call, job, task, source_set, activation, transport


def checked_packet(db, p, packet, *, allow_pending=False):
    values = _inventory(db, p, packet.invocation_id); actual, command, call, job, task, source_set, activation, transport = values
    need(actual.id == packet.id)
    if job.status == 'running' and activation['status'] == 'completed' and transport.get('phase') == 'succeeded':
        if allow_pending: raise ProducerPending(job.id, call.id)
        need(False, '原资料包生产任务仍未终结；未先采用尚未完成的来源')
    need(job.status == 'succeeded' and activation['status'] == 'completed' and transport.get('phase') == 'succeeded'
        and same(activation['output'], command.data['output']) and transport.get('invocation_id') == call.id)
    return {'packet_id': packet.id, 'evidence_id': packet.evidence_id, 'sha256': packet.sha256, 'task_id': packet.task_id,
        'producer_job_id': job.id, 'invocation_id': call.id, 'source_set_id': source_set.id,
        'source_set_hash': source_set.source_set_hash, 'producer_hash': digest(receipt_audit_data(call, job, command.data['output']))}


def read_original_context(db, p, call):
    values = _inventory(db, p, call.id); packet, command = values[:2]
    need(packet.invocation_id == call.id)
    return copy.deepcopy(command.data['output']['context_result'])


def reconcile(db, p, cap, inputs, key, approval, call):
    validate_call_approval(db, p, cap, 'data', inputs, key, approval)
    packet, command, actual = _inventory(db, p, call.id)[:3]
    need(actual.idempotency_key == key and same(inputs, packet.data['source_input']) and same(approval, packet.data['approval']))
    return copy.deepcopy(command.data['output'])


def declared_sources(db, p, task):
    if task.data.get('execution_mode') != 'confirmed_graph_workflow': return False
    _, graph, _ = sources.task_context(db, p, task)
    return bool(sources.ports(graph))


def is_family_evidence(db, p, task, identity):
    evidence = scoped(db, m.Evidence, identity, p)
    if KEY in evidence.data: return True
    if db.scalar(select(Packet.id).where(Packet.workspace_id == p.workspace_id, Packet.evidence_id == identity)): return True
    if db.scalar(select(Command.id).where(Command.workspace_id == p.workspace_id,
        Command.data['output']['packet']['evidence_id'].as_string() == identity)): return True
    if db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == PRODUCED,
        m.Audit.data['evidence_id'].as_string() == identity)): return True
    if db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == p.workspace_id,
        m.Invocation.data['capability_snapshot']['execution']['adapter'].as_string() == ADAPTER,
        m.Invocation.data['output']['packet']['evidence_id'].as_string() == identity)): return True
    if task.data.get('execution_mode') == 'confirmed_graph_workflow':
        from . import workflow_graph
        _, graph, _ = sources.task_context(db, p, task)
        producers = {node['id'] for node in graph['nodes'] if node['kind'] == 'ability' and node['execution'].get('adapter') == ADAPTER}
        for activation in task.data.get('graph_runtime', {}).get('activations', {}).values():
            output = activation.get('output')
            if activation['node_id'] in producers and type(output) is dict and identity in output.get('evidence_ids', []): return True
    return False


def uses_family(db, p, task, evidence_ids):
    return declared_sources(db, p, task) or any(is_family_evidence(db, p, task, identity) for identity in evidence_ids)


def selected_packets(db, p, task, evidence_ids):
    need(type(evidence_ids) is list and 1 <= len(evidence_ids) <= 3 and len(set(evidence_ids)) == len(evidence_ids))
    required = declared_sources(db, p, task); result = []
    for identity in evidence_ids:
        if not is_family_evidence(db, p, task, identity): continue
        rows = list(db.scalars(select(Packet).where(Packet.workspace_id == p.workspace_id, Packet.evidence_id == identity)))
        need(len(rows) == 1 and rows[0].task_id == task.id, '原运行资料包缺少同一任务的独立生产库存，不能当普通上传绕过')
        row = rows[0]
        _, approved = approved_set(db, p, task, {'source_set_id': row.source_set_id, 'source_set_hash': row.data['selected']['source_set_hash']})
        need(same(approved, row.data['selected']))
        result.append(row)
    need(not required or bool(result), '已批准运行文档来源的任务须真正采用本任务资料包，不能只发送普通上传')
    return result


def qualify_consumer(db, p, task, evidence_ids, *, allow_pending=False):
    if not uses_family(db, p, task, evidence_ids): return []
    return [checked_packet(db, p, row, allow_pending=allow_pending) for row in selected_packets(db, p, task, evidence_ids)]


def get_packet(db, p, identity):
    packet = scoped(db, Packet, identity, p); proof = checked_packet(db, p, packet)
    source_set = scoped(db, sources.SourceSet, packet.source_set_id, p)
    evidence, raw = evidence_bytes(db, p, packet.evidence_id)
    task = scoped(db, m.Task, packet.task_id, p)
    _, graph, _ = sources.task_context(db, p, task)
    from . import business_reasoning_v197 as reasoning, workflow_evidence_consent_wait_v199 as consent
    from .business_reasoning_protocols_v276 import for_adapter
    options = []; seen = set()
    for node in graph['nodes']:
        execution = node.get('execution', {})
        if node['kind'] != 'ability': continue
        protocol = for_adapter(execution.get('adapter'))
        if protocol is None: continue
        cap = scoped(db, m.Capability, execution['capability_id'], p)
        try:
            need(cap.content_hash == execution['capability_hash'] and cap.status == 'published')
            model = protocol.validate_manifest(db, p, cap.data)
        except (HTTPException, ValueError, KeyError, TypeError): continue
        if model.id in seen: continue
        seen.add(model.id); grants = consent._grants(db, evidence, model)
        current = sources.native.context(db, p, task.data['instance_id'])
        managed = sources.approval.managed(db, p, task)
        allowed = bool(current['can_upload'] and current['release_id'] == task.data['instance_release_id']
            and managed is not None and managed.status == 'executing')
        options.append({'id': model.id, 'name': model.data.get('name') or model.name, 'hash': model.content_hash,
            'authorized': bool(grants), 'can_authorize': allowed and not grants,
            'reason': None if allowed else '当前任务或执行批准不允许新增外发同意'})
    return {'schema': 'workflow-document-packet-view-v207', 'id': packet.id, 'task_id': packet.task_id,
        'evidence_id': packet.evidence_id, 'sha256': packet.sha256, 'source_set': sources.source_view(source_set),
        'context_result': read_original_context(db, p, scoped(db, m.Invocation, packet.invocation_id, p)),
        'text': raw.decode('utf-8'), 'evidence_version': evidence.version, 'model_options': options,
        'unreviewed': True, 'created_at': packet.created_at.astimezone(timezone.utc).isoformat()}


def list_packets(db, p, identity):
    scoped(db, m.Task, identity, p)
    rows = list(db.scalars(select(Packet).where(Packet.workspace_id == p.workspace_id,
        Packet.task_id == identity).order_by(Packet.created_at.desc(), Packet.id.desc()).limit(101)))
    with sources.native.verification_scope(db): values = [get_packet(db, p, row.id) for row in rows[:100]]
    return {'schema': 'workflow-document-packet-list-v207', 'task_id': identity, 'records': values, 'has_more': len(rows) > 100}
