"""Audited V288 evaluation packet store; no model, receiver or new acquisition.

Upstream completion is read from original SDK/Job/audits. The current producer
is still running while saving; only the ordinary graph worker completes it.
"""
import copy
import re
from fastapi import HTTPException
from sqlalchemy import func, select

from . import goal_evaluation_query_v288 as queries
from . import workflow_goal_evaluation_contracts_v288 as contract
from . import workflow_goal_evaluation_packet_v288 as ports
from . import workflow_goal_data_route_runtime_v288 as local
from . import workflow_graph as graph_runtime, workflow_graph_scope as scope
from . import workflow_service_v195 as service, workflow_task_context_v196 as reader
from .contracts import digest
from .evidence import save_evidence, evidence_bytes
from .models import Acquisition, Audit, Capability, Event, Invocation, Job, Memory, Observation, Task
from .services import audit, validate
from .temporal import instant, matches
from .typed_payload_codec_v131 import canonical_json
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288, row_values

OP = ports.OP
KEY = 'workflow_goal_evaluation_packet_v288'
STATE = 'workflow-goal-evaluation-packet-state-v288'
CREATED = 'workflow.goal_evaluation_packet.created_v288'
FOREIGN = ('service_state', local.KEY, 'workflow_decision_plan_v277', 'workflow_decision_review_plan_v285')
PRIVATE_TEXT = re.compile(
    r'''(?ix)\b(?:proxy-authorization|authorization|cookie|set-cookie|x-api-key|api-key)["']?\s*[:=]
    |\b(?:bearer|basic)\s+\S+
    |\b(?:password|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret[_-]?key)["']?\s*[:=]
    |\b(?:https?|rtsp[s]?|ftp[s]?|smb)://[^\s/@]+:[^\s/@]+@''')
need, checked, fresh, hashed = ports.need, queries.checked, queries._fresh, queries.hashed


def _inputs(value):
    validate(ports.input_schema(), value)
    return copy.deepcopy(value)


def _binding(task, release, business, graph, node):
    from . import authored_goal_data_workflow_store_v288 as family
    value = graph_runtime.business_binding(release, business)
    need(value.get('schema') == 'authored-goal-data-workflow-binding-v288'
        and value['workspace_id'] == task.workspace_id and release.id == task.data['instance_release_id']
        and release.instance_id == task.data['instance_id'] and release.package_id == task.package_id
        and family.check_binding(value) == value and value['graph'] == graph
        and node['id'] == 'evaluation_packet' and value['capability_pins'][node['id']] == node['execution']
        and family.service_delegations(value)[node['id']] == {
            'service_actor': 'reader_service', 'operation': OP, 'agent_role': 'data'})
    return value


def _cold_node(db, p, task, activation_id, expected_node):
    actual, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, activation_id)
    need(actual.id == task.id and node['id'] == expected_node and node['kind'] == 'ability'
        and activation['status'] == 'completed')
    transport = actual.data['graph_jobs'][activation_id]
    job = fresh(db, p, Job, transport['job_id']); call = fresh(db, p, Invocation, transport['invocation_id'])
    cap = fresh(db, p, Capability, call.capability_id); scope._job_matches(actual, job, state, activation, node)
    need(call.status == 'succeeded' and job.data['task_id'] == task.id and job.data['actor_id'] == node['actor_id']
        and transport.get('phase') == 'succeeded' and transport.get('output_hash') == digest(activation['output'])
        and call.data.get('output') == activation['output'] and cap.content_hash == cap.validated_hash == digest(cap.data)
        and cap.release_version == cap.data['version'] == node['execution']['release_version']
        and cap.data['input_schema'] == node['execution']['input_schema']
        and cap.data['output_schema'] == node['execution']['output_schema']
        and call.data.get('capability_snapshot') == cap.data)
    approval = service.make_approval(actual, job, node, job.data['inputs'])
    graph_runtime.check_invocation(call, p, cap, node, job.data['inputs'], job,
        transport.get('dispatch_ownership'), approval=approval)
    local._native_audits(db, p, actual, job, activation, call, job.data['inputs'])
    local._success(db, p, actual, job, activation, call)
    if job.status == 'running': raise reader.ProducerPending(job.id, call.id)
    need(job.status == 'succeeded')
    return {'task': actual, 'job': job, 'call': call, 'cap': cap, 'node': node, 'activation': activation,
        'release': release, 'business': business, 'graph': graph, 'state': state, 'approval': approval}


def _pin(source):
    job, call = source['job'], source['call']
    return {'job_id': job.id, 'invocation_id': call.id, 'actor_id': job.data['actor_id'],
        'request_key_hash': digest(call.idempotency_key)}


def _upstream(db, p, task, job, inputs):
    actual, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
    scope._job_matches(actual, job, state, activation, node); binding = _binding(actual, release, business, graph, node)
    edge = activation['inputs_snapshot']['bindings']['local_plan']
    need(edge['missing'] is False and edge['value_hash'] == digest(inputs['local_plan'])
        and inputs == job.data['inputs'] == activation['inputs_snapshot']['arguments'])
    source = _cold_node(db, p, actual, edge['producer_activation_id'], 'local_prepare')
    plan = local.saved_result(db, p, actual, source['job'], source['call'], source['cap'], source['job'].data['inputs'])
    need(plan == inputs['local_plan'] and plan['route'] in {'acquire', 'data_ready'})
    reader_result = source['job'].data['inputs']['context_result']
    need(plan['decision']['reader_result_hash'] == digest(reader_result))
    trigger = {'kind': 'data_ready', 'local_plan_hash': hashed(plan),
        'reader_invocation_id': plan['producer']['invocation_id'], 'reader_result_hash': hashed(reader_result),
        'reader_context_hash': None, 'resense_invocation_id': None, 'resense_receipt_hash': None}
    resense = None; barrier = source
    if plan['route'] == 'acquire':
        from . import workflow_resense_goal_receipts_v271 as receipts
        from . import workflow_job_dependencies_v264 as dependencies
        candidates = [item for item in state['activations'].values()
            if item['node_id'] == 'resense' and item['epoch'] == activation['epoch'] and item['status'] == 'completed']
        need(len(candidates) == 1)
        barrier = _cold_node(db, p, actual, candidates[0]['id'], 'resense')
        need(barrier['job'].data['inputs'] == {'context': plan['resense_context']})
        bound = job.data[dependencies.KEY]
        dependencies._proof(db, p, dependencies.BOUND, job.id, bound['bound_by'], bound)
        need(all(bound[key] == item for key, item in dependencies._child_pin(job).items()))
        control = dependencies.checked_control(db, p, actual, record_hash=bound['control_record_hash'])
        need(control is not None and barrier['job'].id in dependencies.graph_parent_ids(actual, control, activation['id'])
            and any(pin == dependencies.parent_pin(db, p, actual, barrier['job'].id) for pin in bound['parents']))
        resense = receipts.saved_receipt(db, p, actual, barrier['job'], barrier['call'], barrier['cap'],
            barrier['job'].data['inputs'], barrier['call'].idempotency_key, barrier['approval'], binding)
        need(resense == barrier['call'].data['output'])
        trigger.update(kind='acquisition_completed', reader_context_hash=plan['resense_context']['context_hash'],
            resense_invocation_id=barrier['call'].id, resense_receipt_hash=resense['service_receipt']['receipt_hash'])
    return {'task': actual, 'binding': binding, 'trigger': trigger, 'plan': plan, 'barrier': _pin(barrier),
        'reader_result': reader_result, 'resense': resense}


@checked
def producer_integrity_pin(db, p, task, job, inputs):
    value = _inputs(inputs)
    need(job.data['actor_id'] == p.user_id)
    return _upstream(db, p, task, job, value)['barrier']


def producer_wait_pin(db, p, task, job, inputs):
    return producer_integrity_pin(db, p, task, job, inputs)


@checked
def validate_call_approval(db, p, cap, role, inputs, key, approval):
    from .leases import fenced
    need(fenced(db) and role == 'data')
    task = fresh(db, p, Task, approval['task_id']); queries.require_supported_goal(task)
    current = service.validate_call_approval(db, p, cap, role, _inputs(inputs), key, approval, _variant=ports)
    _upstream(db, p, current['task'], current['job'], inputs)
    return current


def _row_hash(row):
    return hashed({'id': row.id, 'version': row.version, 'data': row.data})


def _resense_items(resense):
    if resense is None:
        return []
    receipt = resense['service_receipt']
    primary = receipt['observations']; followups = receipt.get('followup_observations', [])
    need(type(primary) is list and bool(primary) and type(followups) is list
        and len(primary) + len(followups) <= 1000)
    items = [*primary, *followups]
    need(all(type(item) is dict and set(item) == {'acquisition_id', 'observation_id', 'data_receipt_hash'}
        for item in items))
    need(len({item['observation_id'] for item in items}) == len(items)
        and len({item['acquisition_id'] for item in items}) == len(items))
    return items


def _acks(db, p, task, state, cut, window, resense=None):
    """Reception is separate from admissibility under the Goal's quality rules.

    Only actual query-admitted records and the already authenticated V271
    collection receipts enter this inventory. Excluded collection records stay
    excluded in the saved query, including outside-window or unconfirmed data.
    """
    from . import original_acquisition_reception_v127 as original, mapped_acquisition_origin_v177 as mapped
    from . import goal_ack_origin_mqtt_v297 as mqtt_origin
    from .instance_data_receipts import observation_receipt
    admitted = state['result']['observation_evidence']
    need(type(admitted) is list and len(admitted) <= 1000)
    by_id = {item['observation_id']: item for item in admitted}
    need(len(by_id) == len(admitted))
    required = {item['observation_id']: item for item in _resense_items(resense)}
    result = []
    for identity in sorted(set(by_id) | set(required)):
        item = by_id.get(identity); requested = required.get(identity)
        obs = fresh(db, p, Observation, identity)
        passive = mqtt_origin.handles(db, p, obs)
        if passive:
            need(item is not None and requested is None,
                '被动MQTT仅作为原查询准入观测，不替代主动补采完成回执')
            source = mqtt_origin.checked_origin(db, p, obs, task=task, cut=cut)
            origin = None; acq = None
        else:
            origin = original.context(db, p, obs)
            need(origin is not None, '此片尚不支持该观测来源，未生成替代Acquisition或ACK')
            source = mapped.guard(db, p, obs, execution=False, result=obs.data['extracted'])
            need(type(source) is dict, '此片尚不支持非V177原映射观测，未猜测其来源或模拟标签')
            acq = origin.acquisition
        received = observation_receipt(db, p, obs)
        reception = received['data_reception']
        event = fresh(db, p, Event, reception['event_id'])
        memories = list(db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
            Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence').limit(2).execution_options(populate_existing=True)))
        need(len(memories) == 1); memory = memories[0]
        attrs = {key: value for key, value in obs.data['extracted']['attributes'].items()
            if key in task.data['goal_spec']['required_fields']}
        need(obs.entity_id in task.data['entity_ids'] and obs.status == 'processed' and event.status == 'received'
            and memory.data['data_reception'] == reception
            and obs.observed_at <= event.received_at <= cut and memory.created_at <= cut
            and obs.created_at <= cut and event.created_at <= cut)
        if not passive:
            need(origin.entity.id == obs.entity_id and source['acquisition_id'] == acq.id
                and origin.task.workspace_id == p.workspace_id and acq.status == 'succeeded'
                and acq.data['closing_observation_id'] == obs.id)
        if item is not None:
            need(obs.version == item['version'] and obs.source_id == item['source_id']
                and obs.entity_id == item['entity_id'] and item['data_reception'] == reception
                and item['observed_at'] == obs.observed_at.isoformat()
                and item['attributes'] == attrs and bool(attrs)
                and matches(obs.observed_at, window, evaluated_at=cut))
        if requested is not None:
            # V195/V271 pin the complete checked observation_receipt wrapper.
            # ACKRef instead pins the inner data_reception receipt below;
            # neither hash can stand in for the other.
            need(origin.task.id == task.id and requested == {'acquisition_id': acq.id,
                'observation_id': obs.id, 'data_receipt_hash': digest(received)})
        cap = fresh(db, p, Capability, obs.source_id); extractor = fresh(db, p, Capability, obs.data['extractor_id'])
        need(reception['source_hash'] == cap.content_hash and reception['extractor_hash'] == extractor.content_hash)
        reference = {'workspace_id': p.workspace_id, 'task_id': task.id,
            'query_admitted': item is not None, 'entity_id': obs.entity_id, 'fields': list(attrs),
            'observation_id': obs.id, 'observation_hash': _row_hash(obs), 'source_id': obs.source_id,
            'source_hash': cap.content_hash, 'extractor_id': extractor.id, 'extractor_hash': extractor.content_hash,
            'event_id': event.id, 'event_hash': _row_hash(event), 'memory_id': memory.id, 'memory_hash': _row_hash(memory),
            'data_reception_hash': reception['receipt_hash'], 'observed_at': obs.observed_at.isoformat(),
            'received_at': event.received_at.isoformat(), 'simulation_only': source.get('simulation') is True,
            'test_only': source.get('test_only') is True}
        if passive:
            reference.update(source)
        else:
            reference.update(origin_task_id=origin.task.id, acquisition_id=acq.id, acquisition_hash=_row_hash(acq))
        result.append(reference)
    return result



def _document(task, record):
    """Public scalar presentation only; full reports and identities stay local."""
    from .entity_release_compat import verify_task_binding_snapshot
    from .workflow_task_packet_v199 import _public, UUID_IN_TEXT
    need(verify_task_binding_snapshot(task) is not None)
    report = record['evaluation_result']['result']; requested = record['evaluation_request']['fields']
    objects = task.data['object_snapshots']; need([row['id'] for row in objects] == record['evaluation_request']['entity_ids'])
    labels = {}; lines = ['独立目标资料评估（仅供核对）', '资料截点：' + record['cut_at'],
        '资料状态：' + {'sufficient': '原查询资料充足，尚不等于事实已核实',
            'insufficient': '资料不足，需要核对', 'conflict': '存在冲突，需要核对'}[report['status']],
        '正文复述原查询与目录，未独立核验值类型、观测单位或语义质量。',
        '本资料不授予采集、执行、数据出域或目标完成权限。']
    if record['simulation_only']: lines.append('模拟资料：不作为真实现场测量或物理状态证明。')
    if record['test_only']: lines.append('测试用途限制：原测试来源标签保留。')
    for row in objects:
        fields = {item['key']: item for item in row['fields']}; attrs = row['attributes']
        names = [item['key'] for item in row['fields'] if item['kind'] == '文本' and item['required']
            and isinstance(attrs.get(item['key']), str) and attrs[item['key']].strip()]
        preferred = [key for key in names if fields[key]['label'].lower() in {'名称', '对象名称', '设备名称', 'name', 'title'}]
        need(len(preferred or names) == 1, '原对象显示名称不唯一，未将内部编号写入证据正文')
        labels[row['id']] = (attrs[(preferred or names)[0]], fields)
        need(all(key in fields for key in requested))
    for title, rows in (('档案原记录', report['evidence']), ('独立收件观测', report['observation_evidence'])):
        need(type(rows) is list and len(rows) <= 1000)
        for row in rows:
            name, fields = labels[row['entity_id']]
            lines.append(title + '｜对象：' + name + '｜观测时间：' + (row.get('observed_at') or '原记录未提供'))
            need(type(row['attributes']) is dict and set(row['attributes']) <= set(requested))
            for key, value in row['attributes'].items():
                field = fields[key]; need(value is None or type(value) in {str, bool, int, float})
                _public({field['label']: value})
                text = ('原记录未提供值' if value is None else '是' if value is True else '否' if value is False
                    else value if type(value) is str else canonical_json(value))
                unit = field.get('unit'); need(unit is None or type(unit) is str)
                suffix = ('；原目录单位：' + unit + '；未据此独立核验本条观测单位，也未转换') if unit else (
                    '；原目录未登记单位，未补猜或转换' if field['kind'] in {'数值', '整数'} else '')
                correct = (value is None or field['kind'] == '文本' and type(value) is str
                    or field['kind'] == '是否' and type(value) is bool
                    or field['kind'] == '整数' and type(value) is int
                    or field['kind'] == '数值' and type(value) in {int, float})
                if not correct: suffix += '；值类型与原目录不能核对'
                lines.append(field['label'] + '：' + text + suffix)
    for row in report['conflicts']:
        name, fields = labels[row['entity_id']]
        lines.append('冲突待核对：' + name + '的' + fields[row['field']]['label'] + '｜时间：' + row['observed_at'])
    for row in report['gaps']:
        name, fields = labels[row['entity_id']]
        missing = row['missing_fields']; need(type(missing) is list and set(missing) <= set(fields))
        lines.append('资料缺口：' + name + '｜' + ('、'.join(fields[key]['label'] for key in missing) or '原查询仍有完整性限制'))
    if report.get('excluded_evidence'):
        lines.append('另有未纳入记录 ' + str(len(report['excluded_evidence'])) + ' 条；没有用其补足资料。')
    content = '\n'.join(lines)
    encoded = content.encode('utf-8'); need(0 < len(encoded) <= 20000, '证据正文超过完整呈现上限，未截断')
    need(PRIVATE_TEXT.search(content) is None, '正文含疑似凭据或HTTP认证资料，未生成可外发证据')
    _public(content)
    need(UUID_IN_TEXT.search(content) is None, '正文包含内部编号，未生成可外发证据')
    return encoded


def _output(row):
    value = row.data; sufficient = value['evaluation_result']['status'] == 'sufficient'; ref = value['evidence_ref']
    return ports.checked_result({'schema': ports.RESULT, 'route': 'data_ready' if sufficient else 'review_required',
        'query_status': value['evaluation_result']['status'], 'reason_code': 'evaluation_reports_sufficient' if sufficient else 'evaluation_requires_review',
        'evaluation_ref': {'id': row.id, 'record_hash': row.record_hash}, 'evidence_ids': [ref['evidence_id']],
        'packet': {'id': row.id, 'version': 1, 'evidence_id': ref['evidence_id'], 'sha256': ref['sha256'],
            'simulation_only': value['simulation_only'], 'proposal_only': True, 'cloud_authorized': False}, **ports.FLAGS})


def _anchor(journal):
    return {'schema': STATE, 'journal_hash': journal['journal_hash'], 'journal': copy.deepcopy(journal)}


def _read(db, p, task, job, call, cap, inputs, approval):
    queries._reader(db, p)
    need(not any(key in call.data for key in FOREIGN), '原调用含其它协议状态，未作为新评价继续执行')
    journal = call.data.get(KEY)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == CREATED, Audit.object_id == call.id).limit(2)))
    row = db.scalar(select(GoalEvaluationV288).where(GoalEvaluationV288.workspace_id == p.workspace_id,
        GoalEvaluationV288.id == call.id).execution_options(populate_existing=True))
    if journal is None:
        need(not events and row is None, '原评价或审计存在但journal缺失，未重建评价或查询')
        return None
    need(row is not None and journal['schema'] == STATE
        and journal['journal_hash'] == hashed({key: value for key, value in journal.items() if key != 'journal_hash'})
        and journal['task_id'] == task.id and journal['job_id'] == job.id and journal['invocation_id'] == call.id
        and journal['capability_id'] == cap.id and journal['capability_hash'] == cap.content_hash
        and journal['input_hash'] == digest(inputs) == call.data['input_hash']
        and journal['approval_hash'] == digest(approval) and call.data['approval'] == approval
        and call.data['inputs'] == inputs and row.record_hash == journal['evaluation_record_hash']
        and len(events) == 1 and events[0].actor_id == job.data['actor_id'] and events[0].data == _anchor(journal))
    frozen = queries.checked_frozen(db, p, task, row.evaluation_query_id, evaluation_id=call.id)
    record = contract.check_record(row.data, expected_scope=frozen['provenance']['scope'],
        goal_snapshot=queries.snapshot(task), preparation_policy=frozen['provenance']['preparation_policy'], trusted_now=row.data['cut_checked_at'])
    for key, value in row_values(record).items(): need(key == 'data' or getattr(row, key) == value)
    upstream = _upstream(db, p, task, job, inputs)
    need(record['trigger'] == upstream['trigger'] and record['evaluation_query_ref'] == frozen['query_ref']
        and record['original_query_ref'] == frozen['provenance']['original_query_ref']
        and record['producer'] == {field: frozen['provenance']['creator'][field] for field in contract.ProducerPin.model_fields}
        and record['producer']['actor_id'] == job.data['actor_id']
        and record['producer']['capability_id'] == cap.id and record['producer']['capability_hash'] == cap.content_hash
        and record['producer']['input_hash'] == call.data['input_hash'] == job.data['input_hash']
        and record['producer']['lease_token'] == call.data['lease_token']
        and record['request_id'] == job.id and record['request_key'] == call.idempotency_key == job.idempotency_key
        and record['ack_refs'] == _acks(db, p, task, frozen['result'], instant(record['cut_at']), record['derived_window'], upstream['resense']))
    evidence, content = evidence_bytes(db, p, record['evidence_ref']['evidence_id'])
    need(content == _document(task, record) and evidence.sha256 == record['evidence_ref']['sha256']
        and evidence.size_bytes == record['evidence_ref']['size_bytes'] and evidence.media_type == 'text/plain')
    output = _output(row); need(output == journal['output'])
    return output


@checked
def execute(db, p, cap, inputs, key, approval):
    value = _inputs(inputs); current = validate_call_approval(db, p, cap, 'data', value, key, approval)
    task, job = current['task'], current['job']; call = service._call(db, p, current, key, value, approval)
    prior = _read(db, p, task, job, call, cap, value, approval)
    if prior is not None: return prior
    upstream = _upstream(db, p, task, job, value); binding = upstream['binding']
    policy = contract.check_preparation_policy(task.data['goal_spec'], binding['preparation_policy'])
    cut = db.scalar(select(func.clock_timestamp())).isoformat()
    query = queries.create_frozen(db, p, task, job, evaluation_id=call.id, cut_at=cut,
        parent_query_id=task.data['native_goal_query_id'], preparation_policy=policy)
    frozen = queries.checked_frozen(db, p, task, query.id, evaluation_id=call.id)
    return materialize_frozen_v294(db, p, current, call, cap, value, approval, frozen)


def materialize_frozen_v294(db, p, current, call, cap, value, approval, frozen):
    """Finish the original locally frozen result after validated live authority.

    Both entry points own the real task lock and worker fence. No new query,
    cut, SDK, cloud call or device action is created by this step.
    """
    task, job = current['task'], current['job']; key = call.idempotency_key
    upstream = _upstream(db, p, task, job, value); binding = upstream['binding']
    policy = contract.check_preparation_policy(task.data['goal_spec'], binding['preparation_policy'])
    provenance = frozen['provenance']
    acknowledgements = _acks(db, p, task, frozen['result'], instant(provenance['cut_at']), provenance['derived_window'], upstream['resense'])
    if upstream['resense'] is not None:
        originals = _resense_items(upstream['resense'])
        # _acks already cold-checked each original wrapper hash and produced
        # its corresponding inner receipt hash. This is an identity coverage
        # check, not an equality between those two different receipt hashes.
        need(bool(originals) and all(any(ack['origin_task_id'] == task.id and ack['acquisition_id'] == item['acquisition_id']
            and ack['observation_id'] == item['observation_id']
            for ack in acknowledgements) for item in originals))
    previous = list(db.scalars(select(GoalEvaluationV288).where(GoalEvaluationV288.workspace_id == p.workspace_id,
        GoalEvaluationV288.task_id == task.id, GoalEvaluationV288.goal_version == task.data['goal_version'])
        .order_by(GoalEvaluationV288.generation).limit(1001)))
    need(len(previous) <= 1000 and [item.generation for item in previous] == list(range(1, len(previous) + 1)))
    record = {'schema': contract.RECORD, 'evaluation_id': call.id, 'scope': provenance['scope'],
        'goal_snapshot': provenance['goal_snapshot'], 'preparation_policy': policy, 'generation': len(previous) + 1,
        'request_id': job.id, 'request_key': key, 'original_query_ref': provenance['original_query_ref'],
        'evaluation_query_ref': frozen['query_ref'], 'evaluation_request': frozen['request'], 'evaluation_result': frozen['result'],
        'query_controls': copy.deepcopy(queries.CONTROLS), 'cut_at': provenance['cut_at'],
        'cut_checked_at': provenance['cut_checked_at'], 'derived_window': provenance['derived_window'],
        'trigger': upstream['trigger'], 'producer': {field: provenance['creator'][field] for field in contract.ProducerPin.model_fields},
        'ack_refs': acknowledgements, 'simulation_only': binding['simulation_only'] or any(item['simulation_only'] for item in acknowledgements),
        'test_only': any(item['test_only'] for item in acknowledgements), **contract.FLAGS}
    evidence = save_evidence(db, p, _document(task, record), 'text/plain', cloud_model_id=None)
    need(evidence.data.get('cloud_model_ids') == [], '本地评价证据不沿用或新增出域授权')
    record['evidence_ref'] = {'evidence_id': evidence.id, 'sha256': evidence.sha256,
        'size_bytes': evidence.size_bytes, 'media_type': evidence.media_type}
    record = contract.seal_record(record, expected_scope=provenance['scope'], goal_snapshot=provenance['goal_snapshot'],
        preparation_policy=policy, trusted_now=provenance['cut_checked_at'])
    row = GoalEvaluationV288(**row_values(record)); db.add(row)
    output = _output(row)
    journal = {'schema': STATE, 'task_id': task.id, 'job_id': job.id, 'invocation_id': call.id,
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'input_hash': digest(value),
        'approval_hash': digest(approval), 'evaluation_record_hash': row.record_hash, 'output': output}
    journal['journal_hash'] = hashed(journal); call.data = {**call.data, KEY: journal}
    audit(db, p, CREATED, call.id, _anchor(journal)); db.flush()
    from .native_goals import queue
    queue(db, p, task, 'goal-evaluation-v288:' + row.id,
        goal_evaluation_ref_v288={'id': row.id, 'record_hash': row.record_hash},
        goal_evaluation_producer_job_v288=job.id)
    return copy.deepcopy(output)


@checked
def reconcile(db, p, cap, inputs, key, approval, call):
    value = _inputs(inputs); current = validate_call_approval(db, p, cap, 'data', value, key, approval)
    actual = service._call(db, p, current, key, value, approval, supplied=call, allow_unknown=True)
    result = _read(db, p, current['task'], current['job'], actual, cap, value, approval)
    if result is not None:
        return result
    from .workflow_goal_evaluation_recovery_v294 import resume
    return resume(db, p, current, actual, cap, value, approval)


@checked
def saved_result(db, p, task, job, call, cap, inputs):
    if job.kind == 'append_goal_evaluation_v297':
        from .workflow_goal_evaluation_append_v299 import saved_result as saved_append
        return saved_append(db,p,task,job,call,cap,inputs)
    with db.no_autoflush:
        source = _cold_node(db, p, task, job.data['activation_id'], 'evaluation_packet')
        need(source['job'].id == job.id and source['call'].id == call.id and source['cap'].id == cap.id)
        result = _read(db, p, source['task'], source['job'], source['call'], source['cap'], _inputs(inputs), source['approval'])
        need(result is not None and result == source['call'].data['output'])
        return result


@checked
def saved_receipt(db, p, cap, inputs, key, approval, call):
    job = fresh(db, p, Job, call.data['job_id']); task = fresh(db, p, Task, job.data['task_id'])
    need(key == call.idempotency_key and approval == call.data['approval'])
    return saved_result(db, p, task, job, call, cap, inputs)


def retained_marker_shape(call):
    try:
        need(call is not None and call.status == 'unknown' and not any(key in call.data for key in FOREIGN))
        ports.validate_manifest(call.data['capability_snapshot']); value = call.data[KEY]
        return (value['schema'] == STATE and value['invocation_id'] == call.id
            and value['capability_id'] == call.capability_id and value['job_id'] == call.data['job_id']
            and value['capability_hash'] == call.data['capability_hash']
            and value['input_hash'] == digest(call.data['inputs']) and value['approval_hash'] == digest(call.data['approval'])
            and value['journal_hash'] == hashed({key: item for key, item in value.items() if key != 'journal_hash'}))
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError):
        return False
