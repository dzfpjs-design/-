"""Read original accepted authority and products; never execute or re-accept."""
import copy
from datetime import timezone
from fastapi import HTTPException
from sqlalchemy import select, or_
from . import models as m
from . import layered_approval_store_v175 as a
from .contracts import digest
from .services import scoped, require
from .layered_approval_contract_v175 import AcceptanceCommand, original_json


def need(ok, message='原已验收任务、资料、独立回执或来源库存不完整；未重建来源', code=409):
    if not ok: raise HTTPException(code, message)


def utc(value): return value.astimezone(timezone.utc).isoformat()


def person(db, identity):
    row = db.get(m.User, identity)
    need(row is not None, '原责任账号记录缺失，未改用其他账号')
    return {'id': row.id, 'name': row.username}


def human_text(value):
    """Show actual scalar content, not an internal JSON editor/identifier dump."""
    if type(value) is str: return value
    if type(value) is bool: return '是' if value else '否'
    if value is None: return '未填写'
    if type(value) in {int, float}: return str(value)
    if type(value) is list: return '\n'.join(human_text(item) for item in value)
    if type(value) is dict:
        labels = {'result': '处理结果', 'note': '说明', 'summary': '摘要', 'decision': '决定',
            'reason': '依据', 'approved': '确认', 'conclusion': '结论', 'review_result': '复核结论'}
        return '\n'.join((labels.get(key, '处理内容') + '：' + human_text(item)) for key, item in value.items())
    need(False)


def support_reason(task):
    if not task.data.get('layered_approval_v175'):
        return '此任务未使用独立层级验收，暂不能整理为本类案例'
    if task.data.get('execution_mode') != 'confirmed_graph_workflow':
        return '本类案例需要已确认执行图、真实运行资料包及独立验收'
    return None


def accepted_source(db, p, identity):
    """Return immutable proof and readable source, separate from current IAM."""
    require(p, 'admin', 'operator', 'viewer')
    task = scoped(db, m.Task, identity, p)
    managed = a.managed(db, p, task)
    if managed is not None:
        need(task.data.get('layered_approval_v175') == managed.id,
            '原任务永久层级审批标记缺失或改变；未降级为其他来源')
    need(support_reason(task) is None, support_reason(task) or '', 422)
    need(managed is not None and managed.status == 'accepted', '请先完成原任务的独立人工验收', 422)
    data = a.checked(managed)
    submission, submitted, spec = a.current_submission(db, p, managed)
    acceptance = data.get('acceptance')
    need(type(acceptance) is dict and acceptance.get('decision') == 'accept'
        and data['acceptance_history'] and data['acceptance_history'][-1] == acceptance)
    commands = list(db.scalars(select(a.Command).where(a.Command.workspace_id == p.workspace_id,
        a.Command.subject_id == task.id, a.Command.operation.like('task_%'))))
    by_id = {row.id: row for row in commands}
    final = by_id[data['command_ids'][-1]]
    result = a.checked_command(db, final)
    body, _, _ = original_json(final.exact_body.encode('utf-8'), AcceptanceCommand)
    basis = acceptance.get('basis'); need(type(basis) is dict)
    core = {key: value for key, value in basis.items() if key != 'basis_hash'}
    need(final.operation == 'task_accept' and body.request_id == acceptance['request_id'] == final.request_id
        and final.actor_id == acceptance['actor_id'] and body.decision == 'accept'
        and body.reason == acceptance['reason'] and body.basis_hash == basis.get('basis_hash')
        and basis['basis_hash'] == a.acceptance_hash(core)
        and basis.get('schema') == 'layered-approval-acceptance-basis-v175'
        and basis.get('task_id') == task.id and basis.get('physical_state_verified') is False
        and type(basis.get('task_version')) is int and basis['task_version'] + 1 == result['task_version'] == task.version
        and basis.get('submission_hash') == submission.data['record_hash'] == result['submission_hash']
        and basis.get('workflow_runtime') == task.data.get('workflow_runtime')
        and basis.get('graph_state_hash') == task.data.get('graph_runtime', {}).get('state_hash')
        and result['status'] == 'accepted' and task.status == 'workflow_completed'
        and result['approval_state']['data']['acceptance'] == acceptance)
    memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        or_(m.Memory.task_id == task.id, m.Memory.data['task_id'].as_string() == task.id),
        m.Memory.data['type'].as_string() == 'layered_approval_acceptance_v175')))
    expected = {'type': 'layered_approval_acceptance_v175', 'task_id': task.id,
        'submission_hash': submission.data['record_hash'], 'acceptance': copy.deepcopy(acceptance),
        'physical_state_verified': False, 'auto_publish': False}
    need(len(memories) == 1 and memories[0].task_id == task.id and memories[0].status == 'candidate'
        and memories[0].version == 1 and memories[0].valid_until is None
        and memories[0].text == '层级审批任务已保存独立人工验收，未自动发布经验或证明物理状态'
        and memories[0].data == expected, '原独立验收候选经验缺失、重复或已变更；不补写可信经验')
    memory = memories[0]
    from .workspace_api import saved_execution
    from .workflow_graph import graph_for
    from .authored_workflow_runtime_v208 import verify_release, business_binding
    _, release, business = saved_execution(db, p, task.id)
    need(release.data['snapshot'].get('semantic_runtime_plan', {}).get('schema') == 'instance-authored-workflow-runtime-v208',
        '此来源不属于人工确认的运行资料业务图，暂不能整理为本类案例', 422)
    verify_release(db, p, release, live=False, business_name=business['name'])
    binding = business_binding(release, business); graph = graph_for(release, business)
    from .full_semantic_graph_v1 import check_run
    state = check_run(graph, task.data['graph_runtime'])
    need(state['status'] == 'succeeded')
    from .workflow_document_task_integrity_v207 import verify_task_results
    verify_task_results(db, p, task, graph)
    from . import workflow_document_packet_v207 as packet, runtime_task_documents as sources
    from . import business_reasoning_v197 as reasoning
    packets = list(db.scalars(select(packet.Packet).where(packet.Packet.workspace_id == p.workspace_id,
        packet.Packet.task_id == task.id).order_by(packet.Packet.id)))
    producers = [node for node in graph['nodes'] if node['kind'] == 'ability' and node['execution']['adapter'] == packet.ADAPTER]
    need(producers and packets and len(packets) == len(producers))
    packet_proofs, documents, packet_views, links = [], [], [], []
    seen_sets = set()
    from .evidence import evidence_bytes
    for row in packets:
        packet_proofs.append(packet.checked_packet(db, p, row))
        source_set = sources.checked_set(db, p, scoped(db, sources.SourceSet, row.source_set_id, p), task=task)
        _, raw = evidence_bytes(db, p, row.evidence_id)
        packet_views.append({'id': row.id, 'evidence_id': row.evidence_id, 'text': raw.decode('utf-8'),
            'source_count': len(source_set.data['entries']), 'unreviewed': True})
        links.append({'label': '查看原任务资料包', 'url': '/api/runtime-task-documents/packets/' + row.id})
        if source_set.id not in seen_sets:
            seen_sets.add(source_set.id)
            for entry in source_set.data['entries']:
                documents.append({'label': entry['source_name'] + ' · ' + entry['location_label'],
                    'kind': entry['kind'], 'text': entry['text'], 'unreviewed': entry['unreviewed'], 'partial': entry['partial']})
                links.append({'label': '查看资料原文定位', 'url': entry['original_citation_url']})
    human_results, model_results, product_proofs = [], [], []
    receipts = task.data['workflow_runtime']['receipts']
    for activation in state['activations'].values():
        node = next(node for node in graph['nodes'] if node['id'] == activation['node_id'])
        if node['kind'] not in {'human', 'ability'}: continue
        need(activation['status'] == 'completed')
        matches = [row for row in receipts if row.get('activation_id') == activation['id']]
        need(len(matches) == 1); receipt = matches[0]
        need(receipt.get('actor') == node['actor_id'] and receipt.get('output') == activation['output'])
        if node['kind'] == 'human':
            need(receipt.get('execution_mode') == 'human_graph_result')
            command_body = {'activation_id': activation['id'], 'output': activation['output'],
                'actor_id': node['actor_id'], 'expected_version': activation['version'] - 1, 'cancel_epoch': activation['epoch']}
            found = [row for row in state['commands'] if row['request_id'] == receipt.get('request_id')]
            events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.object_id == task.id, m.Audit.action == 'workflow.graph.human_completed',
                m.Audit.data['activation_id'].as_string() == activation['id'])))
            need(len(found) == len(events) == 1 and found[0]['op'] == 'complete'
                and found[0]['actor_id'] == node['actor_id'] and found[0]['body_hash'] == digest(command_body)
                and events[0].actor_id == node['actor_id'] and events[0].data == {'activation_id': activation['id']})
            human_results.append({'step_name': next(item['name'] for item in binding['step_mapping'] if item['node_id'] == node['id']),
                'actor': person(db, node['actor_id']), 'result': human_text(activation['output'])})
            product_proofs.append({'receipt': receipt, 'command': found[0], 'audit_id': events[0].id, 'audit_data': events[0].data})
        elif node['execution']['adapter'] == reasoning.ADAPTER:
            transport = task.data['graph_jobs'][activation['id']]
            call = scoped(db, m.Invocation, transport['invocation_id'], p)
            job = scoped(db, m.Job, transport['job_id'], p)
            cap = scoped(db, m.Capability, node['execution']['capability_id'], p)
            saved = reasoning.saved_result(db, p, task, job, call, cap, job.data['inputs'])
            candidates = {row['candidate_ref']: row['summary'] for row in job.data['inputs']['candidates']}
            analysis = saved['analysis']
            model_results.append({'summary': analysis['summary'], 'candidates': [{'proposal': candidates[row['candidate_ref']],
                'assessment': row['assessment'], 'reason': row['reason']} for row in analysis['candidate_assessments']],
                'limitations': ['模型分析是待人工核对的建议，不是事实确认、审批或设备执行授权']})
            product_proofs.append({'invocation_id': call.id, 'invocation_data': copy.deepcopy(call.data), 'job_id': job.id,
                'job_data': copy.deepcopy(job.data)})
        else:
            need(node['execution']['adapter'] == packet.ADAPTER)
    boundaries = ['独立人工工程验收不证明现场对象状态或真实业务效果',
        '资料包只包含明确选取的片段，未逐字核对，不代表整份资料完整内容',
        '整理或批准案例不会发布可信事实、自动改变规则、调用模型或操作设备']
    if any(row['kind'] == 'ocr' for row in documents): boundaries.append('机器识别文字尚未核对识别准确性')
    if any(row['partial'] for row in documents): boundaries.append('来源包含部分正文或局部识别结果')
    proof = {'schema': 'layered-case-origin-v209', 'task_id': task.id, 'task_version': task.version,
        'task_data_hash': digest(task.data), 'managed_authority': a.authority(managed),
        'accept_command_id': final.id, 'accept_command_sha256': final.body_sha256,
        'memory_id': memory.id, 'memory_data_hash': digest(memory.data), 'memory_status': memory.status,
        'release_id': release.id, 'configuration_hash': release.configuration_hash,
        'binding_hash': digest(binding), 'packet_proofs': packet_proofs, 'product_proofs': product_proofs}
    fingerprint = digest(proof)
    source = {'task_id': task.id, 'task_title': task.title, 'task_version': task.version,
        'business_name': business['name'], 'memory_id': memory.id, 'source_fingerprint': fingerprint,
        'accepted_at': acceptance['at'], 'accepted_by': person(db, acceptance['actor_id']),
        'acceptance_reason': acceptance['reason'], 'physical_state_verified': False, 'boundaries': boundaries,
        'documents': documents, 'packets': packet_views, 'model_results': model_results,
        'human_results': human_results, 'evidence_links': links}
    return task, managed, spec, proof, source


def reviewer(db, p, managed, original_spec, *, lock=False):
    """Current grants for the original acceptance post; no administrator bypass."""
    require(p, 'admin', 'operator')
    a.real_member(db, p, p.user_id, lock_sources=lock)
    policy = a.current_policy(db, p, managed.policy_id, lock_sources=lock)
    _, current_spec = a.policy_value(db, p, policy, current=True)
    need(current_spec['scope_id'] == original_spec['scope_id'], '原业务组织范围已改变，不能据此审核旧案例', 403)
    return a.role_allowed(db, p, original_spec['acceptance'], lock_sources=lock)
