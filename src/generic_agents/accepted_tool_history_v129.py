"""SELECT-only provenance checks for an already saved human acceptance.

Frozen results remain the hash authority. These checks compare them with the
original Job/Invocation/Plan, never today's assessment, objects or publication
status. Legacy and native-step executions did not store a second independent
lease receipt: their Invocation lease is checked for presence and original Job
linkage, not compared with the Job's mutable current lease. Graph executions
additionally have a frozen dispatch lease/authorization to compare exactly.
"""
import copy
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import Audit, Capability, CloudCall, Invocation, Job, Plan
from .services import scoped, validate as validate_value


ERROR = '原正式验收的工具调用、输入输出或原派发证明无法核对'


def _need(value):
    if not value:
        raise HTTPException(409, ERROR)


def _same(left, right):
    return digest(left) == digest(right)


def _lease(call, job):
    _need(call.data.get('job_id') == job.id
        and type(call.data.get('lease_token')) is str and bool(call.data['lease_token']))


def _normalized(receipt, name, *, graph=False):
    return {'status': 'succeeded', 'method': 'registered_capability',
        'capability_id': receipt['capability_id'], 'capability_name': name,
        'capability_hash': receipt['capability_hash'], 'invocation_id': receipt['invocation_id'],
        'input_hash': receipt['input_hash'], 'output': copy.deepcopy(receipt['output']),
        'workflow_step_id': receipt['step_id'], 'workflow_receipt_hash': digest(receipt),
        'candidate_only': True if graph else receipt.get('candidate_only', False),
        'physical_state_verified': False}


def _graph(db, p, task, release, business, expected, required):
    from . import full_semantic_graph_v1 as pure
    from . import workflow_graph as runtime
    from . import workflow_graph_scope as scope
    from .workflow_graph_goal_tools_v126 import _AUTH_KEYS, _RESULT_KEYS, _time
    binding = runtime.business_binding(release, business)
    graph = binding['graph']
    state = pure.check_run(graph, task.data['graph_runtime'])
    _need(state['status'] == 'succeeded' and business['execution_mode'] == 'confirmed_graph_workflow')
    projection = task.data['workflow_runtime']
    _need(projection['schema'] == 'workflow-graph-projection-v1' and projection['current_step_id'] is None)
    candidates = projection['receipts']
    _need(type(candidates) is list and all(type(item) is dict and item.get('execution_mode')
        in {'registered_graph_ability', 'human_graph_result'} for item in candidates))
    nodes = {node['id']: node for node in graph['nodes']}
    ability = [item for item in candidates if item['execution_mode'] == 'registered_graph_ability']
    completed = {identity for identity, value in state['activations'].items()
        if value['status'] == 'completed' and nodes[value['node_id']]['kind'] == 'ability'}
    _need(len(ability) == len(completed) and {item['activation_id'] for item in ability} == completed)
    result = []
    for receipt in ability:
        _need(set(receipt) == _RESULT_KEYS and receipt['candidate_only'] is True
            and receipt['entity_state_updated'] is False)
        activation = state['activations'][receipt['activation_id']]
        node = nodes[activation['node_id']]; execution = node['execution']
        _need(_same(binding['capability_pins'][node['id']], execution)
            and receipt['step_id'] == (node['source_step_id'] or node['id'])
            and receipt['actor'] == node['actor_id']
            and receipt['capability_id'] == execution['capability_id']
            and receipt['capability_hash'] == execution['capability_hash']
            and receipt['release_version'] == execution['release_version']
            and _same(receipt['output'], activation['output'])
            and _same(receipt['input_snapshot'], activation['inputs_snapshot']['arguments'])
            and receipt['input_hash'] == digest(receipt['input_snapshot']))
        job = scoped(db, Job, receipt['request_id'], p)
        transport = scope._job_matches(task, job, state, activation, node)
        attempt = transport.get('attempt')
        _need(type(attempt) is int and 1 <= attempt <= execution['max_attempts'])
        key = 'graph:' + task.id + ':' + activation['id'] + ':' + str(attempt)
        _need(job.idempotency_key == key == job.data['invocation_key']
            and job.data.get('attempt') == attempt and transport.get('phase') == 'succeeded'
            and transport.get('dispatch_committed') is True
            and transport.get('invocation_id') == receipt['invocation_id']
            and transport.get('output_hash') == digest(receipt['output'])
            and transport.get('dispatch_input_hash') == receipt['input_hash'])
        owner, authorization = transport.get('dispatch_ownership'), transport.get('dispatch_authorization')
        _need(type(owner) is dict and set(owner) == {'job_id', 'lease_token'}
            and owner['job_id'] == job.id and type(owner['lease_token']) is str and bool(owner['lease_token'])
            and type(authorization) is dict and set(authorization) == _AUTH_KEYS
            and type(authorization['epoch']) is int and authorization['epoch'] == activation['epoch']
            and authorization['job_id'] == job.id and authorization['lease_token'] == owner['lease_token']
            and authorization['input_hash'] == receipt['input_hash']
            and authorization['capability_hash'] == execution['capability_hash']
            and authorization['actor_id'] == node['actor_id'])
        cap = scoped(db, Capability, execution['capability_id'], p)
        call = scoped(db, Invocation, receipt['invocation_id'], p)
        runtime.check_invocation(call, p, cap, node, receipt['input_snapshot'], job, owner)
        _need(call.status == 'succeeded' and _same(call.data['output'], receipt['output'])
            and ('inputs' not in call.data or _same(call.data['inputs'], receipt['input_snapshot'])))
        for stamp in (call.data['started_at'], authorization['authorized_at'], receipt['at']):
            _time(stamp)
        command = {'request_id': job.id, 'op': 'complete', 'actor_id': node['actor_id'],
            'body_hash': digest({'activation_id': activation['id'], 'output': receipt['output'],
                'actor_id': node['actor_id'], 'expected_version': activation['version'] - 1,
                'cancel_epoch': activation['epoch']})}
        _need(sum(item == command for item in state['commands']) == 1)
        validate_value(execution['input_schema'], receipt['input_snapshot'])
        validate_value(execution['output_schema'], receipt['output'])
        if receipt['capability_id'] in required:
            _need(job.status == 'succeeded')
            frozen = [item for item in expected if item['invocation_id'] == call.id]
            _need(len(frozen) == 1)
            # The display name belongs to the saved acceptance, not a mutable
            # capability description. IDs/hashes and every output are checked.
            result.append(_normalized(receipt, frozen[0]['capability_name'], graph=True))
    return result


def _legacy(db, p, task, business, required):
    result = []
    steps = {item['id']: item for item in business['flow']['steps']}
    for index, receipt in enumerate(task.data['workflow_runtime']['receipts']):
        mode = receipt.get('execution_mode')
        if mode != 'registered_ability':
            _need(mode is None and not any(key in receipt for key in ('invocation_id', 'capability_id', 'input_snapshot')))
            continue
        step = steps[receipt['step_id']]; execution = step['execution']
        _need(step['agent'] != '人工确认' and receipt['capability_id'] == execution['capability_id']
            and receipt['capability_hash'] == execution['capability_hash']
            and receipt['release_version'] == execution['release_version']
            and receipt['input_hash'] == digest(receipt['input_snapshot'])
            and receipt['entity_state_updated'] is False and type(receipt.get('candidate_only', False)) is bool)
        job = scoped(db, Job, receipt['request_id'], p)
        original = job.data['scope']; attempt = original['attempt']
        _need(type(attempt) is int and attempt >= 1)
        key = 'workflow:' + task.id + ':' + str(index) + ':' + str(attempt)
        _need(job.kind == 'execute_workflow_step' and job.data['task_id'] == task.id
            and job.data['actor_id'] == receipt['actor'] and job.idempotency_key == key == job.data['invocation_key']
            and original['step_id'] == step['id'] and original['visit'] == index
            and original['input_hash'] == receipt['input_hash']
            and _same(original['inputs'], receipt['input_snapshot']))
        call = scoped(db, Invocation, receipt['invocation_id'], p)
        _lease(call, job)
        _need(call.status == 'succeeded' and call.capability_id == execution['capability_id']
            and call.idempotency_key == key and call.data.get('role') == execution['agent_role']
            and call.data.get('capability_hash') == execution['capability_hash']
            and call.data.get('input_hash') == receipt['input_hash']
            and call.data.get('approval') is None and call.data.get('side_effect') is False
            and _same(call.data['output'], receipt['output'])
            and ('inputs' not in call.data or _same(call.data['inputs'], receipt['input_snapshot'])))
        validate_value(execution['output_schema'], receipt['output'])
        if receipt['capability_id'] in required:
            result.append(_normalized(receipt, execution['capability_name']))
    return result


def _stopped_candidate(db, p, task, plan, run):
    """Recognize the actual public stop, never guess an earlier JSON preimage.

    The old approval stores a whole candidate hash, not a separable progress
    hash. After a real stop that preimage is unavailable. Immutable semantics
    are instead checked through the approved proposal/context and the original
    cloud preview/receipt. The old cloud receipt has no independent content-byte
    hash; raw JSON semantics are compared with the approved proposal, without
    claiming a nonexistent signature over the original answer's whitespace.
    """
    from . import native_step_planning as planning
    from .workflow_graph_goal_tools_v126 import _time
    _need(run['status'] in {'rejected', 'cancelled'} and plan.status == 'cancelled')
    stop = plan.data['stop']
    _need(type(stop) is dict and set(stop) == {'request_id', 'actor_id', 'reason', 'at'}
        and all(type(stop[key]) is str and bool(stop[key]) for key in stop)
        and bool(stop['reason'].strip()) and str(UUID(stop['request_id'])) == stop['request_id'])
    _time(stop['at'])
    state = planning._state(task)
    _need(state.get('commands_hash') == digest(state['commands']))
    commands = [item for item in state['commands'] if item.get('request_id') == stop['request_id']]
    _need(len(commands) == 1)
    command = commands[0]
    _need(set(command) == {'request_id', 'body_hash', 'actor_id', 'operation', 'target', 'plan_id', 'abandoned', 'at'}
        and command['operation'] == 'stop' and command['actor_id'] == stop['actor_id']
        and command['target'] == plan.id == command['plan_id'] and command['abandoned'] is False
        and type(command['body_hash']) is str and len(command['body_hash']) == 64
        and all(char in '0123456789abcdef' for char in command['body_hash']))
    _time(command['at'])
    audit = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == plan.id, Audit.action == 'native_step_plan.stopped',
        Audit.actor_id == stop['actor_id'], Audit.data['reason'].as_string() == stop['reason']).limit(1))
    _need(audit is not None and audit.workspace_id == p.workspace_id and audit.object_id == plan.id
        and audit.action == 'native_step_plan.stopped' and audit.actor_id == stop['actor_id']
        and audit.data == {'reason': stop['reason']})
    frozen = plan.data['frozen']
    _need(frozen['preview_hash'] == digest({key: value for key, value in frozen.items() if key != 'preview_hash'}))
    cloud = scoped(db, CloudCall, plan.data['cloud_call_id'], p)
    link = cloud.data['native_step_plan']
    _need(type(link) is dict and set(link) == {'task_id', 'plan_id', 'job_id', 'preview_hash'}
        and link['task_id'] == task.id and link['plan_id'] == plan.id
        and link['preview_hash'] == frozen['preview_hash']
        and cloud.capability_id == frozen['model_profile_id']
        and cloud.data['actor_id'] == plan.data['actor_id']
        and cloud.data['request_hash'] == digest(frozen['messages'])
        and cloud.data['dispatch_options_hash'] == digest(frozen['options']))
    source_job = scoped(db, Job, link['job_id'], p)
    _need(source_job.kind == planning.JOB_KIND
        and source_job.idempotency_key == 'native-step-plan:' + plan.id
        and source_job.data['plan_id'] == plan.id and source_job.data['task_id'] == task.id
        and source_job.data['actor_id'] == plan.data['actor_id'])
    raw = plan.data['raw_response']
    _need(type(raw) is dict and set(raw) == {'content', 'receipt'}
        and plan.data['raw_response_hash'] == digest(raw)
        and _same(raw['receipt'], cloud.data['receipt'])
        and raw['receipt']['call_id'] == cloud.id
        and raw['receipt']['provider'] == frozen['model_identity']['provider']
        and raw['receipt']['model'] == frozen['model_identity']['model'])
    answer = json.loads(raw['content'])
    validate_value(frozen['schema'], answer)
    proposal = plan.data['proposal']
    _need(answer['summary'] == proposal['summary'] and len(answer['steps']) == len(proposal['steps']))
    for original, approved in zip(answer['steps'], proposal['steps']):
        # Both prepare_step and the fixed non-tool normalizer replace model
        # titles. Their saved approved values are protected by proposal_hash;
        # a future title table/description must not re-author an old approval.
        _need(type(original) is dict and all(key in approved and _same(value, approved[key])
            for key, value in original.items() if key != 'title'))


def _native_contract(db, p, task, release, business, package, plan, run):
    from . import native_step_execution as execution
    from .business_tools import evidence_hash
    contract = plan.data['execution_contract']
    _need(contract['schema'] == execution.SCHEMA and contract['task_id'] == task.id and contract['plan_id'] == plan.id
        and plan.approved_hash == digest(contract) == run['approval_hash']
        and plan.approved_by == contract['actor_id'] == run['actor_id']
        and digest(plan.data['proposal']) == contract['proposal_hash'])
    candidate_hash = digest({key: value for key, value in plan.data.items() if key not in {'execution_contract', 'record_hash'}})
    if candidate_hash != contract['candidate_record_hash']:
        _stopped_candidate(db, p, task, plan, run)
    context = contract['context']; prefix = context['workflow_prefix_length']
    _need(type(prefix) is int and 0 <= prefix <= len(task.data['workflow_runtime']['receipts']))
    step = next(item for item in business['flow']['steps'] if item['id'] == context['step_id'])
    expected = {key: value for key, value in plan.data['frozen']['context'].items()
        if key not in {'actor_id', 'remaining_steps', 'remaining_calls'}}
    expected['workflow_prefix_length'] = prefix
    _need(_same(context, expected) and step['agent'] == '人工确认'
        and context['task_id'] == task.id and context['owner_id'] == task.owner_id
        and context['instance_id'] == task.data['instance_id']
        and context['release_id'] == release.id and context['configuration_hash'] == release.configuration_hash
        and context['package_id'] == task.package_id and context['package_hash'] == package.content_hash
        and context['business_id'] == business['id'] and context['step_hash'] == digest(step)
        and context['workflow_prefix_hash'] == digest(task.data['workflow_runtime']['receipts'][:prefix])
        and _same(context['goal_snapshot'], task.data['native_goal_context']['goal_snapshot'])
        and context['native_goal_context_hash'] == task.data['native_goal_context_hash']
        and context['inputs_hash'] == digest({key: task.data.get(key) for key in ['object_snapshots',
            'material_snapshots', 'image_snapshots', 'scene_snapshots', 'comparison_scene_snapshots',
            'audio_snapshots', 'perception_event', 'perception_signal']})
        and context['evidence_hash'] == evidence_hash(contract['evidence'])
        and context['evidence_status'] == contract['evidence']['status'])
    grants = step.get('planning_tools', [])
    _need(context['tools'] == [{'id': item['capability_id'], 'hash': item['capability_hash'],
        'version': item['release_version']} for item in grants])
    _need(run['id'] == plan.id and run['step_id'] == step['id']
        and len(run['steps']) == len(contract['steps']))
    return contract


def _native(db, p, task, release, business, accepted):
    from . import native_step_execution as execution
    from . import native_step_planning as planning
    ledger = execution._ledger(task)
    runs = [ledger['runs'][identity] for identity in sorted(ledger['runs'])]
    saved = accepted['evaluation'].get('tool_execution')
    _need(saved is not None or not runs)
    if saved is not None:
        _need(_same(runs, saved['runs']))
    package = scoped(db, Capability, task.package_id, p) if runs else None
    receipts, proofs = [], []
    for run in runs:
        plan = planning.checked(db, p, run['id'], task)
        contract = _native_contract(db, p, task, release, business, package, plan, run)
        proof = {'plan_id': run['id'], 'version': run['version'], 'status': run['status'],
            'approval_hash': run['approval_hash'], 'result_hash': run['result_hash'],
            'review': copy.deepcopy(run['review']), 'history_hash': digest(run['history']),
            'started_steps_hash': digest(run['started_steps'])}
        proofs.append(proof)
        _need(run['status'] in {'accepted', 'rejected', 'cancelled'})
        if run['status'] != 'accepted':
            continue
        _need(run['review']['decision'] == 'accepted' and run['result_hash'] == execution._result_hash(run, contract)
            and all(item['status'] == 'succeeded' for item in run['steps']))
        original_jobs = run['job_ids']
        _need(type(original_jobs) is list and len(set(original_jobs)) == len(original_jobs))
        queue_commands = [item for item in ledger['commands'] if item['plan_id'] == plan.id
            and item['operation'] in {'approve', 'retry'} and item['abandoned'] is False]
        _need(len(queue_commands) == len(original_jobs))
        verified = []
        for index, step in enumerate(contract['steps']):
            if step['action'] != 'invoke_capability':
                continue
            result = run['steps'][index]
            _need(result['index'] == index and result['action'] == step['action']
                and result['input'] == step['input_snapshot'] and step['input_hash'] == digest(step['input_snapshot']))
            call = scoped(db, Invocation, result['invocation_id'], p)
            checked = execution._call_result(db, p, task, plan, copy.deepcopy(run), index, call)['steps'][index]
            _need(_same(checked, result) and call.status == 'succeeded' and call.data.get('side_effect') is False)
            job = scoped(db, Job, call.data['job_id'], p)
            _lease(call, job)
            _need(job.id in original_jobs)
            serial = original_jobs.index(job.id)
            _need(job.kind == execution.JOB_KIND and job.data['task_id'] == task.id
                and job.data['plan_id'] == plan.id == job.data['run_id']
                and job.data['approval_hash'] == plan.approved_hash
                and job.data['actor_id'] == queue_commands[serial]['actor_id']
                and job.idempotency_key == 'native-step-execution:' + plan.id + ':' + str(serial)
                and any(item['index'] == index and item['attempt'] == result['attempt'] for item in run['started_steps']))
            verified.append({'status': 'succeeded', 'method': 'registered_capability',
                'capability_id': step['capability_id'], 'output': copy.deepcopy(call.data['output']),
                'invocation_id': call.id, 'input_hash': step['input_hash'], 'output_hash': digest(call.data['output']),
                'capability_hash': step['capability_hash'], 'release_version': step['release_version'],
                'plan_id': plan.id, 'run_id': run['id'], 'approval_hash': plan.approved_hash,
                'result_hash': run['result_hash'], 'goal_snapshot': contract['context']['goal_snapshot']})
        receipts.extend(verified)
        proof['verified_receipts_hash'] = digest(verified)
    signature = digest(proofs)
    if saved is not None:
        _need(saved['all_reviewed'] is True and saved['basis_hash'] == signature)
    return receipts, signature, bool(runs)


def validate(db, p, task, accepted, tool_basis):
    """Check actual historical records; return no authority and perform no writes."""
    try:
        from .workspace_api import saved_execution
        from .accepted_tool_basis_v129 import accepted_tool_basis
        actual, release, business = saved_execution(db, p, task.id)
        _need(actual.workspace_id == p.workspace_id and actual.id == task.id and _same(actual.data, task.data)
            and _same(accepted, task.data['native_goal_acceptance'])
            and _same(tool_basis, accepted_tool_basis(task.data['goal_spec'], accepted['evaluation'])))
        required = {item['capability_id'] for item in task.data['goal_spec']['criteria']
            if item['kind'] in {'capability_success', 'tool_value'}}
        expected = [] if tool_basis is None else tool_basis['workflow_results']
        graph_marked = 'graph_runtime' in task.data or business.get('execution_mode') == 'confirmed_graph_workflow'
        if graph_marked:
            _need('graph_runtime' in task.data and business.get('execution_mode') == 'confirmed_graph_workflow')
            workflow = _graph(db, p, task, release, business, expected, required)
        else:
            workflow = _legacy(db, p, task, business, required)
        native, execution_hash, has_runs = _native(db, p, task, release, business, accepted)
        _need(_same(workflow, expected)
            and _same([*workflow, *native], accepted['evaluation'].get('tool_results', [])))
        if tool_basis is None:
            _need(not required and not has_runs and not workflow)
        else:
            _need(tool_basis['execution_basis'] == execution_hash)
    except HTTPException as error:
        if error.status_code in {403, 409}:
            raise
        raise HTTPException(409, ERROR) from None
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, OverflowError, RecursionError):
        raise HTTPException(409, ERROR) from None
