"""Read-only context from one actual leased graph call, never a dispatch grant.

The gateway independently anchors the complete successful output with PRODUCED.
Historical consumers must check that anchor and the real completed activation;
self hashes and a caller-supplied Task identifier cannot confer authority.
"""
import copy
import re

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import DataRequest, digest
from .models import Audit, DataGap, DataRun, InstanceRelease, Invocation, Job, Membership, MonitorPolicy, User
from .services import published, scoped, validate
from . import workflow_graph_scope as graph_scope
from . import observation_requests as requests
from . import business_flow_review_store as safe

HANDLER = 'workflow_task_context_v196'
RESULT = 'workflow-task-context-result-v196'
EVIDENCE = 'workflow-task-evidence-package-v196'
CONTEXT = 'workflow-resense-context-v196'
PRODUCED = 'workflow.task_context.produced_v196'
PRODUCED_SCHEMA = 'workflow-task-context-produced-v196'
MAX_GAPS = 100
MAX_POLICIES = 200
MAX_CANDIDATES = 1000
MAX_EVIDENCE = 10000
MAX_BYTES = 2 * 1024 * 1024
FLAGS = {'permissions_verified': False, 'can_dispatch': False}
SHA = re.compile(r'^[0-9a-f]{64}$')
ERROR = '当前图任务、原资料缺口、批准策略或独立上下文回执无法核对；未派发设备动作'


class ProducerPending(Exception):
    """A committed output/activation exists, but its original Job is not final."""
    def __init__(self, job_id, invocation_id):
        self.job_id = job_id; self.invocation_id = invocation_id
        super().__init__('原上下文回执已保存，等待同一后台任务完成')


def need(ok):
    if not ok: raise HTTPException(409, ERROR)


def _same(a, b): return safe._same(a, b)


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _schemas():
    text = {'type': 'string', 'minLength': 1}
    ident = {'type': 'string', 'pattern': r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'}
    sha = {'type': 'string', 'pattern': r'^[0-9a-f]{64}$'}
    positive = {'type': 'integer', 'minimum': 1}
    strings = {'type': 'array', 'items': text, 'maxItems': 1000}
    producer = _object({**{key: ident for key in ('capability_id', 'invocation_id', 'job_id', 'actor_id')},
        'capability_hash': sha, 'activation_id': text, 'node_id': text,
        'cancel_epoch': {'type': 'integer', 'minimum': 0}, 'input_hash': sha, 'request_key_hash': sha})
    scope = _object({**{key: ident for key in ('workspace_id', 'task_id', 'instance_id', 'release_id', 'package_id')},
        'business_id': text, **{key: sha for key in ('graph_hash', 'source_binding_hash',
            'goal_snapshot_hash', 'native_goal_context_hash')}})
    query = _object({'query_id': ident, 'request_hash': sha, 'goal_delegation_hash': sha,
        'version_at_read': positive, 'result_hash_at_read': sha})
    gap = _object({'gap_id': ident, 'entity_id': ident, 'version_at_read': positive, 'fields': strings,
        'observation_request': {'type': 'object'}, 'observation_request_hash': sha})
    policy = _object({'policy_id': ident, 'approved_hash': sha, 'approved_by': ident,
        'version_at_read': positive, 'source_pins': {'type': 'array', 'minItems': 1, 'maxItems': 200,
            'items': _object({'source_id': ident, 'source_hash': sha})}})
    context = _object({'schema': {'const': CONTEXT}, 'scope': scope, 'query': query,
        'gap': gap, 'policy': policy, 'producer': producer,
        **{key: {'const': False} for key in FLAGS}, 'context_hash': sha})
    evidence_ref = _object({'kind': {'enum': ['archive', 'observation']}, 'entity_id': ident,
        'observation_id': {'anyOf': [ident, {'type': 'null'}]},
        'version': {'anyOf': [positive, {'type': 'null'}]},
        'observed_at': {'anyOf': [text, {'type': 'null'}]}, 'evidence_hash': sha})
    gap_ref = _object({'gap_id': ident, 'entity_id': ident, 'status': text, 'version_at_read': positive,
        'fields': strings, 'request_hash': sha})
    evidence = _object({'schema': {'const': EVIDENCE}, 'scope': scope, 'query': query,
        'evidence_status': text, 'entity_ids': {'type': 'array', 'items': ident, 'maxItems': 1000},
        'fields': strings, 'evidence_refs': {'type': 'array', 'items': evidence_ref, 'maxItems': MAX_EVIDENCE},
        'gap_refs': {'type': 'array', 'items': gap_ref, 'maxItems': MAX_GAPS}, 'producer': producer,
        **{key: {'const': False} for key in FLAGS}, 'package_hash': sha})
    return context, evidence


def input_schema():
    pin = _object({'policy_id': {'type': 'string', 'pattern': r'^[0-9a-f-]{36}$'},
        'approved_hash': {'type': 'string', 'pattern': r'^[0-9a-f]{64}$'}})
    return _object({'policy_pin': {**pin, 'type': ['object', 'null']}})


def context_schema(): return copy.deepcopy(_schemas()[0])


def output_schema():
    context, evidence = _schemas()
    return _object({'schema': {'const': RESULT}, 'status': {'enum': ['ready', 'waiting_goal', 'needs_selection', 'no_gap', 'blocked']},
        'evidence_package': {**evidence, 'type': ['object', 'null']},
        'resense_context': {**context, 'type': ['object', 'null']},
        'candidates': {'type': 'array', 'items': context, 'maxItems': MAX_CANDIDATES},
        'issues': {'type': 'array', 'maxItems': 1000, 'items': _object({
            'code': {'type': 'string', 'pattern': r'^[a-z_]{1,80}$'},
            'path': {'type': 'string'}, 'message': {'type': 'string', 'minLength': 1, 'maxLength': 500}})},
        **{key: {'const': False} for key in FLAGS}})


def validate_manifest(manifest):
    need(type(manifest) is dict and manifest.get('kind') == 'tool'
        and manifest.get('allowed_agent_roles') == ['data']
        and _same(manifest.get('execution'), {'adapter': 'builtin', 'handler': HANDLER, 'side_effect': False})
        and _same(manifest.get('input_schema'), input_schema())
        and _same(manifest.get('output_schema'), output_schema()))
    return copy.deepcopy(manifest)


def _sealed(value, key):
    return {**copy.deepcopy(value), key: digest(value)}


def _check_context(value):
    safe._bytes(value, MAX_BYTES); validate(context_schema(), value)
    need(value['context_hash'] == digest({k: v for k, v in value.items() if k != 'context_hash'})
        and value['gap']['observation_request_hash'] == digest(value['gap']['observation_request']))
    for key in ('gap_id', 'query_id', 'entity_id'):
        actual = value['gap']['observation_request'].get(key)
        expected = value['query']['query_id'] if key == 'query_id' else value['gap'][key]
        need(actual == expected)
    request = value['gap']['observation_request']
    need(_same(request.get('fields'), value['gap']['fields'])
        and request.get('query_request_hash') == value['query']['request_hash']
        and digest(request.get('goal_delegation')) == value['query']['goal_delegation_hash']
        and request.get('goal_delegation', {}).get('task_id') == value['scope']['task_id'])
    return value


def checked_result(value):
    safe._bytes(value, MAX_BYTES); validate(output_schema(), value)
    package = value['evidence_package']
    if package is not None:
        need(package['package_hash'] == digest({k: v for k, v in package.items() if k != 'package_hash'}))
    seen = set()
    for candidate in value['candidates']:
        _check_context(candidate); need(candidate['context_hash'] not in seen); seen.add(candidate['context_hash'])
        need(package is not None and all(_same(candidate[key], package[key]) for key in ('scope', 'query', 'producer')))
    if value['status'] == 'ready':
        context = _check_context(value['resense_context'])
        need(any(_same(context, item) for item in value['candidates']))
    else: need(value['resense_context'] is None)
    return copy.deepcopy(value)


def _inputs(value):
    validate(input_schema(), value); safe._bytes(value, 8192)
    if value['policy_pin'] is not None:
        safe.identity(value['policy_pin']['policy_id'])
    return value


def _result(status, package=None, candidates=None, context=None, code=None):
    value = {'schema': RESULT, 'status': status, 'evidence_package': package,
        'resense_context': context, 'candidates': candidates or [], 'issues': [], **FLAGS}
    if code:
        value['issues'].append({'code': code, 'path': '/resense_context', 'message': {
            'goal_not_ready': '原任务尚未产生已核对的正式目标资料查询。',
            'no_open_gap': '原任务当前没有可新增补采的未结缺口。',
            'selection_required': '请明确选择原任务的真实缺口与已批准策略；系统未自动取第一项。',
            'policy_not_available': '所选策略未能匹配原任务的当前缺口和批准范围。',
            'goal_existing_only': '原目标只允许既有证据，未提供设备派发上下文。'}[code]})
    return checked_result(value)


def _checked_source_binding(binding):
    if binding.get('schema') in {'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'}:
        from .authored_workflow_store_v208 import check_binding
        return check_binding(binding)
    if binding.get('schema') == 'authored-service-workflow-binding-v270':
        from .authored_service_workflow_store_v270 import check_binding
        return check_binding(binding)
    need(binding.get('schema') == 'business-flow-nested-execution-binding-v195')
    return binding


def _binding(db, p, task):
    from .workflow_graph import business_binding
    release = scoped(db, InstanceRelease, task.data['instance_release_id'], p)
    businesses = [item for item in release.data['snapshot']['businesses'] if item['id'] == task.data['business_id']]
    need(len(businesses) == 1 and release.instance_id == task.data['instance_id'] and release.package_id == task.package_id)
    binding = business_binding(release, businesses[0])
    _checked_source_binding(binding)
    return release, binding


def _run(db, p, task, goal):
    from . import native_goals
    identifier = task.data.get('native_goal_query_id')
    if identifier is None: return None
    row = scoped(db, DataRun, identifier, p)
    need(row.data.get('native_goal_task_id') == task.id
        and row.data.get('native_goal_context_hash') == digest(goal)
        and row.data.get('native_goal_request_hash') == digest(row.data.get('request'))
        and _same(row.data.get('goal_delegation'), {'task_id': task.id, 'goal_snapshot': native_goals.snapshot(task)}))
    request = row.data['request']; prepared = {'resolved_time', 'reference_at', 'clarifications'}
    try: checked = DataRequest.model_validate({k: v for k, v in request.items() if k not in prepared}).model_dump()
    except ValueError: need(False)
    need(_same(checked, {k: v for k, v in request.items() if k not in prepared})
        and request['mode'] == 'structured' and request['model_profile_id'] is None
        and request['tool_steps'] == [] and request['allow_external_tools'] is False
        and request['entity_ids'] == task.data['goal_spec']['entity_ids']
        and request['fields'] == task.data['goal_spec']['required_fields']
        and _same(request['resolved_time'], task.data['goal_window']))
    result = row.data.get('result')
    need(type(result) is dict and digest(result) == row.data.get('native_goal_result_hash'))
    if 'native_goal_result_history' in row.data:
        need(digest(row.data['native_goal_result_history']) == row.data.get('native_goal_result_history_hash'))
    need(row.status != 'cancelled')
    return row


def _gaps(db, p, task, run):
    ids = run.data.get('data_gap_ids'); need(type(ids) is list and len(ids) <= MAX_GAPS and len(ids) == len(set(ids)))
    result = []
    for identifier in ids:
        gap = scoped(db, DataGap, identifier, p)
        need(gap.query_id == run.id and gap.entity_id in task.data['entity_ids']
            and gap.data.get('business_task_id') == task.id
            and type(gap.data.get('missing_fields')) is list
            and set(gap.data['missing_fields']).issubset(task.data['goal_spec']['required_fields']))
        result.append(gap)
    return result


def _source_pins(db, p, policy):
    ids = policy.data.get('source_ids'); need(type(ids) is list and 0 < len(ids) <= 200 and len(ids) == len(set(ids)))
    return [{'source_id': row.id, 'source_hash': row.content_hash}
        for row in (published(db, p, identifier, 'source') for identifier in ids)]


def _policy(db, p, policy, request):
    from .perception import authorized_monitor
    from .sensing_selection import validate_policy
    authorized_monitor(db, p, policy)
    need(bool(policy.data.get('selection_requirement')))
    validate_policy(db, p, policy.data)
    requests.effective(db, p, policy, request, dispatch=True)
    return {'policy_id': policy.id, 'approved_hash': policy.data['approved_hash'], 'approved_by': policy.approved_by,
        'version_at_read': policy.version, 'source_pins': _source_pins(db, p, policy)}


def execute(db, p, cap, inputs, key, *, _variant=None):
    """No add/flush/commit/propose; all identity derives from the actual graph."""
    from . import native_goals
    _inputs(inputs)
    if _variant is None:
        validate_manifest(cap.data)
    else:
        # An installed local packet producer may reuse the actual context
        # reader. It does not substitute a capability/Task or create consent.
        from . import workflow_task_packet_v199 as packet
        need(_variant is packet)
        packet.validate_manifest(cap.data)
    task, job, node, frozen = graph_scope.graph_context(db, p, key, cap)
    need(node['execution']['agent_role'] == 'data' and _same(job.data['inputs'], inputs))
    release, binding = _binding(db, p, task)
    call = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key).execution_options(populate_existing=True))
    need(call is not None and call.status == 'running' and call.data.get('job_id') == job.id)
    producer = {'capability_id': cap.id, 'capability_hash': cap.content_hash, 'invocation_id': call.id,
        'job_id': job.id, 'activation_id': frozen['activation_id'], 'node_id': frozen['node_id'],
        'cancel_epoch': frozen['cancel_epoch'], 'input_hash': digest(inputs), 'request_key_hash': digest(key), 'actor_id': p.user_id}
    if not task.data.get('native_goal_context'): return _result('waiting_goal', code='goal_not_ready')
    goal = native_goals.guard(db, p, task, key, lock_sources=False)
    run = _run(db, p, task, goal)
    if run is None: return _result('waiting_goal', code='goal_not_ready')
    gaps = _gaps(db, p, task, run)
    scope = {'workspace_id': p.workspace_id, 'task_id': task.id, 'instance_id': release.instance_id,
        'release_id': release.id, 'package_id': release.package_id, 'business_id': task.data['business_id'],
        'graph_hash': frozen['graph_hash'], 'source_binding_hash': binding['binding_hash'],
        'goal_snapshot_hash': digest(native_goals.snapshot(task)), 'native_goal_context_hash': digest(goal)}
    query = {'query_id': run.id, 'request_hash': digest(run.data['request']), 'goal_delegation_hash': digest(run.data['goal_delegation']),
        'version_at_read': run.version, 'result_hash_at_read': digest(run.data['result'])}
    refs = []; evidence = run.data['result'].get('result', {})
    for kind, rows in (('archive', evidence.get('evidence', [])), ('observation', evidence.get('observation_evidence', []))):
        need(type(rows) is list)
        for row in rows:
            need(type(row) is dict and row.get('entity_id') in task.data['entity_ids'])
            refs.append({'kind': kind, 'entity_id': row['entity_id'], 'observation_id': row.get('observation_id'),
                'version': row.get('version'), 'observed_at': row.get('observed_at'), 'evidence_hash': digest(row)})
    need(len(refs) <= MAX_EVIDENCE)
    package = _sealed({'schema': EVIDENCE, 'scope': scope, 'query': query,
        'evidence_status': run.data['result']['status'], 'entity_ids': copy.deepcopy(run.data['request']['entity_ids']),
        'fields': copy.deepcopy(run.data['request']['fields']), 'evidence_refs': refs,
        'gap_refs': [{'gap_id': gap.id, 'entity_id': gap.entity_id, 'status': gap.status,
            'version_at_read': gap.version, 'fields': copy.deepcopy(gap.data['missing_fields']),
            'request_hash': digest(gap.data['request'])} for gap in gaps], 'producer': producer, **FLAGS}, 'package_hash')
    open_gaps = [gap for gap in gaps if gap.status not in {'resolved', 'cancelled', 'needs_review', 'pending_delivery'}]
    if not open_gaps: return _result('no_gap', package, code='no_open_gap')
    if task.data['goal_spec']['observation_mode'] != 'allow_new':
        return _result('blocked', package, code='goal_existing_only')
    policies = list(db.scalars(select(MonitorPolicy).where(MonitorPolicy.workspace_id == p.workspace_id,
        MonitorPolicy.status == 'active')))
    need(len(policies) <= MAX_POLICIES)
    candidates = []
    for gap in open_gaps:
        request = requests.pin(db, p, gap)
        need(request['goal_delegation']['task_id'] == task.id and request['query_id'] == run.id)
        for policy in policies:
            if gap.entity_id not in policy.data.get('entity_ids', []) or not set(request['fields']).issubset(policy.data.get('fields', [])): continue
            try: policy_pin = _policy(db, p, policy, request)
            except HTTPException as error:
                if error.status_code not in {403, 404, 409, 422}: raise
                continue
            context = _sealed({'schema': CONTEXT, 'scope': scope, 'query': query,
                'gap': {'gap_id': gap.id, 'entity_id': gap.entity_id, 'version_at_read': gap.version,
                    'fields': copy.deepcopy(request['fields']), 'observation_request': copy.deepcopy(request),
                    'observation_request_hash': digest(request)}, 'policy': policy_pin,
                'producer': producer, **FLAGS}, 'context_hash')
            candidates.append(context); need(len(candidates) <= MAX_CANDIDATES)
    pin = inputs['policy_pin']
    selected = [row for row in candidates if pin is not None and row['policy']['policy_id'] == pin['policy_id']
        and row['policy']['approved_hash'] == pin['approved_hash']]
    if pin is not None and len(open_gaps) == 1 and len(selected) == 1:
        return _result('ready', package, candidates, selected[0])
    return _result('needs_selection', package, candidates, code='selection_required' if pin is None or len(open_gaps) > 1 else 'policy_not_available')


def receipt_audit_data(call, job, output):
    checked_result(output)
    need(call.status == 'succeeded' and job.kind == graph_scope.KIND
        and call.data.get('job_id') == job.id and call.idempotency_key == job.data['invocation_key']
        and call.data['input_hash'] == job.data['input_hash'] and _same(call.data['output'], output))
    return {'schema': PRODUCED_SCHEMA, 'invocation_id': call.id, 'job_id': job.id, 'task_id': job.data['task_id'],
        'capability_id': call.capability_id, 'capability_hash': call.data['capability_hash'],
        'input_hash': call.data['input_hash'], 'output_hash': digest(output)}


def _checked_producer_context(db, p, value, *, task):
    """Same original SDK/PRODUCED/Goal/Q0 proof, without acquiring a Gap lock."""
    from . import native_goals
    _check_context(value); scope = value['scope']; producer = value['producer']
    need(scope['workspace_id'] == p.workspace_id == task.workspace_id and scope['task_id'] == task.id)
    call = scoped(db, Invocation, producer['invocation_id'], p); job = scoped(db, Job, producer['job_id'], p)
    need(call.status == 'succeeded' and job.status in {'running', 'succeeded'}
        and call.capability_id == producer['capability_id'] and call.data.get('capability_hash') == producer['capability_hash']
        and call.data.get('role') == 'data' and call.data.get('job_id') == job.id
        and call.data.get('input_hash') == producer['input_hash'] == job.data.get('input_hash')
        and digest(call.idempotency_key) == producer['request_key_hash'] and job.data.get('actor_id') == producer['actor_id'])
    raw_output = call.data.get('output')
    packet_producer = (type(raw_output) is dict
        and raw_output.get('schema') == 'workflow-task-evidence-packet-receipt-v199')
    if packet_producer:
        from . import workflow_task_packet_v199 as packet
        original = checked_result(packet.read_original_context(db,p,call))
        produced_action = packet.PRODUCED
        produced_data = packet.receipt_audit_data(call,job,raw_output)
    else:
        original = checked_result(raw_output)
        produced_action = PRODUCED
        produced_data = receipt_audit_data(call,job,original)
    need(any(_same(value, item) for item in [original['resense_context'], *original['candidates']] if item is not None))
    original_task, release, business, graph, state, activation, node = graph_scope._frozen(
        db, p, task.id, producer['activation_id'])
    graph_scope._job_matches(original_task, job, state, activation, node)
    need(node['id'] == producer['node_id'] and node['actor_id'] == producer['actor_id']
        and node['execution']['capability_id'] == call.capability_id
        and node['execution']['capability_hash'] == producer['capability_hash']
        and node['execution']['agent_role'] == 'data' and activation['epoch'] == producer['cancel_epoch']
        and activation['status'] == 'completed' and _same(activation['output'], raw_output))
    from .workflow_graph import business_binding
    binding = business_binding(release, business)
    _checked_source_binding(binding)
    need(release.id == scope['release_id'] and release.instance_id == scope['instance_id']
        and release.package_id == scope['package_id'] and binding['binding_hash'] == scope['source_binding_hash']
        and state['graph_hash'] == scope['graph_hash']
        and task.data['business_id'] == scope['business_id']
        and digest(task.data.get('native_goal_context')) == scope['native_goal_context_hash']
        and digest(native_goals.snapshot(task)) == scope['goal_snapshot_hash'])
    actor = db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == producer['actor_id'], User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator'])))
    need(actor == producer['actor_id'])
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == produced_action, Audit.object_id == call.id)))
    need(len(events) == 1 and events[0].actor_id == producer['actor_id']
        and _same(events[0].data, produced_data))
    completed = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'workflow.graph.node_completed', Audit.object_id == task.id)))
    matched = [event for event in completed if event.data.get('activation_id') == producer['activation_id']]
    need(len(matched) == 1 and matched[0].actor_id == producer['actor_id']
        and _same(matched[0].data, {'activation_id': producer['activation_id'], 'invocation_id': call.id}))
    if job.status == 'running': raise ProducerPending(job.id, call.id)
    run = scoped(db, DataRun, value['query']['query_id'], p)
    need(task.data.get('native_goal_query_id') == run.id and run.data.get('native_goal_task_id') == task.id
        and digest(run.data['request']) == value['query']['request_hash']
        and digest(run.data.get('goal_delegation')) == value['query']['goal_delegation_hash'])
    return run


def checked_context(db, p, value, *, task, dispatch=True):
    """Read the original producer; consumer authority remains a separate guard."""
    run = _checked_producer_context(db, p, value, task=task)
    gap, actual_run = requests.guard(db, p, value['gap']['observation_request'], dispatch=dispatch)
    need(gap.id == value['gap']['gap_id'] and actual_run.id == run.id and gap.id in run.data['data_gap_ids']
        and gap.data.get('business_task_id') == task.id)
    policy = scoped(db, MonitorPolicy, value['policy']['policy_id'], p)
    from .perception import authorized_monitor
    from .sensing_selection import validate_policy
    authorized_monitor(db, p, policy); validate_policy(db, p, policy.data)
    need(policy.data.get('approved_hash') == value['policy']['approved_hash']
        and policy.approved_by == value['policy']['approved_by'] and policy.data.get('selection_requirement')
        and _same(_source_pins(db, p, policy), value['policy']['source_pins']))
    requests.effective(db, p, policy, value['gap']['observation_request'], dispatch=dispatch)
    return copy.deepcopy(value)


def read_original_context_v290(db, p, value, *, task):
    """Grantless cold proof for the fixed failed V288 resense recovery API.

    This reads the same original SDK/PRODUCED/Goal/Q0 proof as checked_context,
    then persisted Gap/request/policy/source pins with SELECTs only. It does not
    call requests.guard/effective or take a Gap lock while API holds Job→Task.
    It grants no proposal, collection or continuation. The running worker and
    actual service execute still use the unchanged full checked_context guard.
    """
    run = _checked_producer_context(db, p, value, task=task)
    need(value['producer']['node_id'] == 'context_reader')
    from .workflow_graph import business_binding
    original_task, release, business, *_ = graph_scope._frozen(db, p, task.id, value['producer']['activation_id'])
    need(business_binding(release, business)['schema'] == 'authored-goal-data-workflow-binding-v288')
    request = value['gap']['observation_request']
    gap = scoped(db, DataGap, value['gap']['gap_id'], p)
    from .native_goals import snapshot
    need(run.status != 'cancelled' and gap.status not in {'cancelled', 'resolved', 'needs_review', 'pending_delivery'}
        and run.data.get('native_goal_context_hash') == digest(task.data.get('native_goal_context'))
        and run.data.get('native_goal_request_hash') == digest(run.data['request'])
        and _same(run.data.get('goal_delegation'), {'task_id': task.id, 'goal_snapshot': snapshot(task)})
        and gap.id == request['gap_id'] and gap.query_id == run.id == request['query_id']
        and gap.id in run.data['data_gap_ids'] and gap.data.get('business_task_id') == task.id
        and gap.entity_id == value['gap']['entity_id'] == request['entity_id']
        and request['entity_id'] in request['request']['entity_ids']
        and request['query_request_hash'] == digest(run.data['request'])
        and _same(request['request'], gap.data['request'])
        and _same(request['goal_delegation'], run.data.get('goal_delegation'))
        and request['fields'] and set(request['fields']).issubset(gap.data['request']['fields'])
        and value['gap']['observation_request_hash'] == digest(request))
    policy = scoped(db, MonitorPolicy, value['policy']['policy_id'], p)
    from .perception import authorized_monitor
    from .sensing_selection import validate_policy
    authorized_monitor(db, p, policy); validate_policy(db, p, policy.data)
    need(policy.data.get('approved_hash') == value['policy']['approved_hash']
        and policy.approved_by == value['policy']['approved_by'] and policy.data.get('selection_requirement')
        and set(request['fields']).issubset(policy.data['fields']) and request['entity_id'] in policy.data['entity_ids']
        and _same(_source_pins(db, p, policy), value['policy']['source_pins']))
    return copy.deepcopy(value)
