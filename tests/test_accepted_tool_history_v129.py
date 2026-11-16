"""SELECT-boundary fixtures, not real PG acceptance or authority evidence.

The graph kernel and the native original-call checker run unchanged. Only the
database row lookup and already-verified release boundary use in-memory rows;
Root's separate HTTP/PG journey covers actual issuance and persistence.
"""
import copy
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents.api import app
from generic_agents import accepted_tool_history_v129 as history
from generic_agents import native_goals, native_goal_tools, native_step_execution as execution
from generic_agents import native_step_planning as planning, workspace_api
from generic_agents import workflow_graph_goal_tools_v126 as graph_tools, workflow_abilities
from generic_agents.accepted_tool_basis_v129 import accepted_tool_basis
from generic_agents.business_tools import evidence_hash
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, CloudCall, Invocation, Job, Plan
from tests.test_accepted_tool_basis_v129 import frozen_evaluation
from tests.test_workflow_graph_goal_tools_v126 import fixture as graph_fixture


AT = '2026-10-20T08:00:00+00:00'


class ReadOnlyDB:
    def add(self, *args): pytest.fail('historical verification wrote a row')
    def flush(self, *args): pytest.fail('historical verification flushed')
    def commit(self, *args): pytest.fail('historical verification committed')
    def execute(self, *args): pytest.fail('historical verification acquired a live lock')


def install(monkeypatch, f):
    def scoped(database, model, identity, principal, lock=False):
        assert lock is False
        row = f.rows.get((model, identity))
        if row is None or row.workspace_id != principal.workspace_id:
            raise HTTPException(404, 'synthetic actual row missing')
        return row
    monkeypatch.setattr(history, 'scoped', scoped)
    monkeypatch.setattr(planning, 'scoped', scoped)
    monkeypatch.setattr(execution, 'scoped', scoped)
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *args: (f.task, f.release, f.business))
    for module, name in [(native_goals, 'assessment'), (native_goals, 'guard'),
            (native_goal_tools, 'workflow_goal_receipts'), (execution, 'goal_gate'),
            (execution, '_context'), (execution, '_contract'), (graph_tools, 'receipts'),
            (graph_tools, 'catalog'), (workflow_abilities, 'bind')]:
        monkeypatch.setattr(module, name, lambda *args, **kwargs: pytest.fail('history consulted current execution'))


def accept(f, *, workflow=(), runs=(), native=()):
    f.accepted = {'evaluation': frozen_evaluation(workflow=workflow, runs=runs, native=native)}
    f.task.data['native_goal_acceptance'] = copy.deepcopy(f.accepted)
    f.basis = accepted_tool_basis(f.task.data['goal_spec'], f.accepted['evaluation'])


def check(f):
    return history.validate(f.db, f.p, f.task, f.accepted, f.basis)


def graph(monkeypatch, mode='parallel'):
    f = graph_fixture(monkeypatch, mode=mode)
    # Freeze the actual V126 normalization before forbidding all live readers.
    original = graph_tools.receipts(f.db, f.p, f.task)
    f.rows = {(Job, row.id): row for row in f.jobs.values()}
    f.rows.update({(Invocation, row.id): row for row in f.calls.values()})
    f.rows[(Capability, f.cap.id)] = f.cap
    accept(f, workflow=original)
    install(monkeypatch, f)
    return f


@pytest.mark.parametrize('mode', ['sequence', 'parallel', 'loop'])
def test_graph_history_uses_each_actual_activation_without_writes_or_current_gates(monkeypatch, mode):
    f = graph(monkeypatch, mode)
    before = copy.deepcopy(f.task.data)
    assert check(f) is None
    assert f.task.data == before and len(f.basis['workflow_results']) == 2


GRAPH_BAD = [
    ('call-output', lambda f: f.calls['invocation_1'].data.update(output={'value': 99, 'again': False})),
    ('call-input', lambda f: f.calls['invocation_1'].data.update(input_hash='0' * 64)),
    ('call-added-input', lambda f: f.calls['invocation_1'].data.update(inputs={'approved': False})),
    ('call-lease', lambda f: f.calls['invocation_1'].data.update(lease_token='another-original-lease')),
    ('call-job', lambda f: f.calls['invocation_1'].data.update(job_id='job_0')),
    ('call-role', lambda f: f.calls['invocation_1'].data.update(role='business')),
    ('call-capability', lambda f: setattr(f.calls['invocation_1'], 'capability_id', 'other')),
    ('call-hash', lambda f: f.calls['invocation_1'].data.update(capability_hash='0' * 64)),
    ('call-status', lambda f: setattr(f.calls['invocation_1'], 'status', 'unknown')),
    ('call-key', lambda f: setattr(f.calls['invocation_1'], 'idempotency_key', 'other')),
    ('call-approval', lambda f: f.calls['invocation_1'].data.update(approval={'forged': True})),
    ('call-effect', lambda f: f.calls['invocation_1'].data.update(side_effect=True)),
    ('job-kind', lambda f: setattr(f.jobs['job_1'], 'kind', 'another-kind')),
    ('job-status', lambda f: setattr(f.jobs['job_1'], 'status', 'failed')),
    ('job-task', lambda f: f.jobs['job_1'].data.update(task_id='other')),
    ('job-actor', lambda f: f.jobs['job_1'].data.update(actor_id='other')),
    ('job-activation', lambda f: f.jobs['job_1'].data.update(activation_id='other')),
    ('job-graph', lambda f: f.jobs['job_1'].data.update(graph_hash='0' * 64)),
    ('job-epoch', lambda f: f.jobs['job_1'].data.update(cancel_epoch=1)),
    ('job-input', lambda f: f.jobs['job_1'].data.update(inputs={'approved': False})),
    ('job-key', lambda f: setattr(f.jobs['job_1'], 'idempotency_key', 'other')),
    ('job-attempt', lambda f: f.jobs['job_1'].data.update(attempt=2)),
    ('transport-lease', lambda f: list(f.transports.values())[1]['dispatch_ownership'].update(lease_token='other')),
    ('authorization-actor', lambda f: list(f.transports.values())[1]['dispatch_authorization'].update(actor_id='other')),
    ('authorization-epoch', lambda f: list(f.transports.values())[1]['dispatch_authorization'].update(epoch=1)),
    ('uncommitted', lambda f: list(f.transports.values())[1].update(dispatch_committed=False)),
    ('command-missing', lambda f: f.task.data['graph_runtime'].update(commands=[])),
    ('missing-job', lambda f: f.rows.pop((Job, 'job_1'))),
]


@pytest.mark.parametrize('name,change', GRAPH_BAD, ids=[item[0] for item in GRAPH_BAD])
def test_graph_history_rejects_independent_actual_record_damage_in_second_same_cap_activation(monkeypatch, name, change):
    f = graph(monkeypatch); frozen = copy.deepcopy(f.accepted)
    change(f)
    with pytest.raises(HTTPException) as error: check(f)
    assert error.value.status_code == 409 and f.accepted == frozen


def test_graph_history_accepts_current_object_publication_and_current_lease_changes(monkeypatch):
    f = graph(monkeypatch)
    f.entity.version += 7; f.entity.data = {'数量': 99}
    f.cap.status = 'retired'; f.cap.content_hash = '0' * 64
    f.cap.data = {'description': '当前说明已变化'}; f.cap.release_version = 'new-release'
    for job in f.jobs.values(): job.lease_token = 'later-current-lease'
    assert check(f) is None


def test_no_tool_basis_does_not_bypass_existing_graph_execution_provenance(monkeypatch):
    f = graph(monkeypatch)
    f.task.data['goal_spec']['criteria'] = [{'kind': 'human_confirmation'}]
    accept(f)
    assert f.basis is None and check(f) is None
    f.calls['invocation_1'].data['output']['value'] = 77
    with pytest.raises(HTTPException): check(f)


def legacy(monkeypatch):
    p = NS(workspace_id='workspace', user_id='later-reader')
    execution_pin = {'capability_id': 'legacy-cap', 'capability_hash': 'a' * 64,
        'release_version': '1.0.0', 'agent_role': 'data', 'capability_name': '原工具',
        'output_schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}},
            'required': ['value'], 'additionalProperties': False}}
    step = {'id': 'step', 'name': '原能力步骤', 'agent': '数据管理', 'execution': execution_pin}
    business = {'id': 'business', 'flow': {'steps': [step]}}
    inputs, output, key = {'value': 1}, {'value': 1}, 'workflow:task:0:1'
    job = NS(id='legacy-job', workspace_id=p.workspace_id, kind='execute_workflow_step',
        status='running', idempotency_key=key, lease_token='later-current-lease',
        data={'task_id': 'task', 'actor_id': 'original-actor', 'invocation_key': key,
            'scope': {'step_id': step['id'], 'visit': 0, 'attempt': 1,
                'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs)}})
    call = NS(id='legacy-call', workspace_id=p.workspace_id, status='succeeded',
        capability_id=execution_pin['capability_id'], idempotency_key=key,
        data={'job_id': job.id, 'lease_token': 'original-lease', 'role': 'data',
            'capability_hash': execution_pin['capability_hash'], 'input_hash': digest(inputs),
            'output': copy.deepcopy(output), 'approval': None, 'side_effect': False})
    receipt = {'request_id': job.id, 'step_id': step['id'], 'actor': 'original-actor',
        'execution_mode': 'registered_ability', 'invocation_id': call.id,
        'capability_id': execution_pin['capability_id'], 'capability_hash': execution_pin['capability_hash'],
        'release_version': execution_pin['release_version'], 'input_snapshot': inputs,
        'input_hash': digest(inputs), 'output': output, 'entity_state_updated': False, 'candidate_only': False}
    task = NS(id='task', workspace_id=p.workspace_id, owner_id='original-owner', package_id='package',
        data={'instance_id': 'instance', 'goal_spec': {'criteria': [{'kind': 'capability_success',
            'capability_id': execution_pin['capability_id']}]},
            'workflow_runtime': {'current_step_id': None, 'receipts': [receipt]}})
    result = {'status': 'succeeded', 'method': 'registered_capability', 'capability_id': call.capability_id,
        'capability_name': execution_pin['capability_name'], 'capability_hash': execution_pin['capability_hash'],
        'invocation_id': call.id, 'input_hash': digest(inputs), 'output': copy.deepcopy(output),
        'workflow_step_id': step['id'], 'workflow_receipt_hash': digest(receipt),
        'candidate_only': False, 'physical_state_verified': False}
    f = NS(db=ReadOnlyDB(), p=p, task=task, business=business, release=NS(id='release'),
        rows={(Job, job.id): job, (Invocation, call.id): call}, job=job, call=call, receipt=receipt)
    accept(f, workflow=[result]); install(monkeypatch, f)
    return f


def test_legacy_original_job_scope_and_real_invocation_pass_without_current_bind(monkeypatch):
    f = legacy(monkeypatch); before = copy.deepcopy(f.task.data)
    assert check(f) is None and f.task.data == before
    # The old acceptance implementation did not require a reaped Job; this is
    # deliberately different from graph required-tool results.
    assert f.job.status == 'running'


@pytest.mark.parametrize('field,value', [('output', {'value': 8}), ('input_hash', '0' * 64),
    ('job_id', 'other-job'), ('role', 'business'), ('lease_token', ''), ('approval', {'fake': True})])
def test_legacy_independent_invocation_damage_is_not_replaced_by_frozen_task_hash(monkeypatch, field, value):
    f = legacy(monkeypatch); f.call.data[field] = value
    with pytest.raises(HTTPException) as error: check(f)
    assert error.value.status_code == 409


@pytest.mark.parametrize('field,value', [('visit', 9), ('inputs', {'value': 8}), ('step_id', 'other')])
def test_legacy_actual_job_scope_damage_fails_closed(monkeypatch, field, value):
    f = legacy(monkeypatch); f.job.data['scope'][field] = value
    with pytest.raises(HTTPException): check(f)


def native(monkeypatch, *, cloud_original=False):
    f = legacy(monkeypatch)
    f.task.data['workflow_runtime']['receipts'] = []
    f.task.data['object_snapshots'] = []
    cap_data = {'description': '原规划工具', 'execution': {'adapter': 'builtin', 'side_effect': False},
        'input_schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}}},
        'output_schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']}}
    cap_hash = digest(cap_data)
    grant = {'capability_id': 'native-cap', 'capability_hash': cap_hash, 'release_version': '1.0.0'}
    human = {'id': 'human', 'name': '原人工步骤', 'agent': '人工确认', 'planning_tools': [grant]}
    f.business['flow']['steps'] = [human]
    package = NS(id='package', workspace_id=f.p.workspace_id, content_hash='b' * 64)
    f.release = NS(id='release', configuration_hash='c' * 64)
    f.task.data['goal_spec'] = {'criteria': [{'kind': 'human_confirmation'}]}
    goal = {'version': 1, 'hash': digest(f.task.data['goal_spec']),
        'spec': copy.deepcopy(f.task.data['goal_spec']), 'time': {'mode': 'archive'}}
    f.task.data['native_goal_context'] = {'goal_snapshot': goal}
    f.task.data['native_goal_context_hash'] = digest(f.task.data['native_goal_context'])
    evidence = {'status': 'sufficient', 'evidence': []}
    context = {'task_id': f.task.id, 'owner_id': f.task.owner_id, 'instance_id': 'instance',
        'release_id': f.release.id, 'configuration_hash': f.release.configuration_hash,
        'package_id': package.id, 'package_hash': package.content_hash, 'business_id': f.business['id'],
        'step_id': human['id'], 'step_name': human['name'], 'step_hash': digest(human),
        'workflow_prefix_length': 0, 'workflow_prefix_hash': digest([]), 'goal_snapshot': goal,
        'native_goal_context_hash': f.task.data['native_goal_context_hash'],
        'inputs_hash': digest({key: f.task.data.get(key) for key in ['object_snapshots', 'material_snapshots',
            'image_snapshots', 'scene_snapshots', 'comparison_scene_snapshots', 'audio_snapshots',
            'perception_event', 'perception_signal']}), 'evidence_hash': evidence_hash(evidence),
        'evidence_status': 'sufficient', 'tools': [{'id': 'native-cap', 'hash': cap_hash, 'version': '1.0.0'}]}
    step = {'title': '调用原工具', 'action': 'invoke_capability', 'agent': 'data',
        'capability_id': 'native-cap', 'capability_hash': cap_hash, 'release_version': '1.0.0',
        'arguments': {'value': 2},
        'input_snapshot': {'value': 2}, 'input_hash': digest({'value': 2}), 'request_key': 'native-original-key',
        'adapter': 'builtin', 'capability_name': '原规划工具'}
    frozen_context = {key: copy.deepcopy(value) for key, value in context.items() if key != 'workflow_prefix_length'}
    frozen_context.update(actor_id='original-actor', remaining_steps=4, remaining_calls=3)
    original = {'schema': planning.SCHEMA, 'proposal': {'summary': '原计划摘要', 'steps': [copy.deepcopy(step)]},
        'frozen': {'context': frozen_context}}
    cloud = source_job = None
    if cloud_original:
        original['proposal']['steps'].append({'title': '整理待人工核对的计划与限制',
            'agent': 'business', 'action': 'report'})
        frozen = original['frozen']
        frozen.update(messages=[{'role': 'user', 'content': '原已批准请求'}], options={'model': 'original-model'},
            model_profile_id='model', model_identity={'provider': 'qwen', 'model': 'original-model'},
            schema={'type': 'object', 'properties': {'summary': {'type': 'string'},
                'steps': {'type': 'array', 'items': {'oneOf': [
                    {'type': 'object', 'additionalProperties': False,
                        'properties': {'title': {'type': 'string', 'minLength': 1}, 'agent': {'const': 'data'},
                            'action': {'const': 'invoke_capability'}, 'capability_id': {'const': 'native-cap'},
                            'arguments': {'type': 'object', 'properties': {'value': {'type': 'integer'}},
                                'required': ['value'], 'additionalProperties': False}},
                        'required': ['title', 'agent', 'action', 'capability_id', 'arguments']},
                    {'type': 'object', 'additionalProperties': False,
                        'properties': {'title': {'type': 'string', 'minLength': 1}, 'agent': {'const': 'business'},
                            'action': {'const': 'report'}}, 'required': ['title', 'agent', 'action']}
                ]}}}, 'required': ['summary', 'steps']})
        frozen['preview_hash'] = digest(frozen)
        receipt = {'call_id': 'original-cloud', 'provider': 'qwen', 'model': 'original-model',
            'request_id': 'original-provider-request', 'usage': {'total_tokens': 10}}
        raw_answer = {'summary': original['proposal']['summary'], 'steps': [
            {'title': '模型对调用的临时称呼', 'agent': 'data', 'action': 'invoke_capability',
                'capability_id': 'native-cap', 'arguments': {'value': 2}},
            {'title': '整理待人工核查报告', 'agent': 'business', 'action': 'report'}]}
        raw = {'content': json.dumps(raw_answer, ensure_ascii=False), 'receipt': receipt}
        original.update(actor_id='original-planner', cloud_call_id='original-cloud', raw_response=raw,
            raw_response_hash=digest(raw), progress='原候选已保存')
        cloud = NS(id='original-cloud', workspace_id=f.p.workspace_id, capability_id='model',
            data={'actor_id': 'original-planner', 'request_hash': digest(frozen['messages']),
                'dispatch_options_hash': digest(frozen['options']), 'receipt': copy.deepcopy(receipt),
                'native_step_plan': {'task_id': f.task.id, 'plan_id': 'plan', 'job_id': 'plan-job',
                    'preview_hash': frozen['preview_hash']}})
        source_job = NS(id='plan-job', workspace_id=f.p.workspace_id, kind=planning.JOB_KIND,
            idempotency_key='native-step-plan:plan', data={'task_id': f.task.id, 'plan_id': 'plan',
                'actor_id': 'original-planner'})
    contract = {'schema': execution.SCHEMA, 'task_id': f.task.id, 'plan_id': 'plan',
        'actor_id': 'original-actor', 'actor_role': 'operator', 'context': context,
        'candidate_record_hash': digest(original), 'proposal_hash': digest(original['proposal']),
        'steps': copy.deepcopy(original['proposal']['steps']), 'evidence': evidence}
    plan = NS(id='plan', workspace_id=f.p.workspace_id, task_id=f.task.id, approved_by='original-actor',
        approved_hash=digest(contract), data={**original, 'execution_contract': contract})
    plan.data['record_hash'] = digest(plan.data)
    run = {'id': plan.id, 'version': 4, 'actor_id': 'original-actor', 'step_id': human['id'],
        'status': 'accepted', 'approval_hash': plan.approved_hash, 'result_hash': None,
        'review': {'decision': 'accepted', 'reason': '已核对原结果', 'actor_id': 'original-reviewer', 'at': AT},
        'history': [], 'started_steps': [{'index': 0, 'attempt': 0, 'at': AT}],
        'job_ids': ['native-job'], 'active_job_id': 'native-job', 'steps': [{
            **execution._step_view(item, index), 'status': 'succeeded', 'attempt': 0,
            **({'output': {'physical_state_verified': False}} if item['action'] == 'report' else {})}
            for index, item in enumerate(contract['steps'])]}
    job = NS(id='native-job', workspace_id=f.p.workspace_id, kind=execution.JOB_KIND,
        status='running', lease_token='later-lease', idempotency_key='native-step-execution:plan:0',
        data={'task_id': f.task.id, 'plan_id': plan.id, 'run_id': plan.id,
            'approval_hash': plan.approved_hash, 'actor_id': 'original-actor'})
    call = NS(id='native-call', workspace_id=f.p.workspace_id, capability_id='native-cap',
        status='succeeded', idempotency_key=step['request_key'], data={'input_hash': step['input_hash'],
            'inputs': copy.deepcopy(step['input_snapshot']), 'output': {'value': 2}, 'role': 'data',
            'capability_hash': cap_hash, 'capability_snapshot': copy.deepcopy(cap_data),
            'approval': execution._approval(plan, run, 0), 'job_id': job.id, 'lease_token': 'original-lease',
            'side_effect': False, 'started_at': AT, 'finished_at': AT, 'duration_ms': 1})
    execution._call_result(f.db, f.p, f.task, plan, run, 0, call)
    run['result_hash'] = execution._result_hash(run, contract)
    ledger = {'schema': execution.SCHEMA, 'runs': {plan.id: run}, 'commands': [{
        'request_id': 'original-approve', 'body_hash': 'd' * 64, 'operation': 'approve',
        'plan_id': plan.id, 'actor_id': 'original-actor', 'abandoned': False, 'at': AT}]}
    ledger['ledger_hash'] = digest(ledger); f.task.data[execution.STATE] = ledger
    normalized = {'status': 'succeeded', 'method': 'registered_capability', 'capability_id': 'native-cap',
        'output': copy.deepcopy(call.data['output']), 'invocation_id': call.id,
        'input_hash': step['input_hash'], 'output_hash': digest(call.data['output']),
        'capability_hash': cap_hash, 'release_version': '1.0.0', 'plan_id': plan.id,
        'run_id': run['id'], 'approval_hash': plan.approved_hash, 'result_hash': run['result_hash'],
        'goal_snapshot': goal}
    f.rows = {(Capability, package.id): package, (Plan, plan.id): plan, (Job, job.id): job, (Invocation, call.id): call}
    if cloud_original:
        f.rows[(CloudCall, cloud.id)] = cloud; f.rows[(Job, source_job.id)] = source_job
        f.cloud, f.source_job = cloud, source_job
    f.plan, f.run, f.job, f.call = plan, run, job, call
    accept(f, runs=[run], native=[normalized]); install(monkeypatch, f)
    return f


def test_native_history_rechecks_original_plan_and_call_using_only_a_copied_run(monkeypatch):
    f = native(monkeypatch); before_task = copy.deepcopy(f.task.data); before_plan = copy.deepcopy(f.plan.data)
    assert check(f) is None
    assert f.task.data == before_task and f.plan.data == before_plan
    assert f.job.status == 'running' and f.job.lease_token != f.call.data['lease_token']


@pytest.mark.parametrize('field,value', [('output', {'value': 9}), ('inputs', {'value': 9}),
    ('input_hash', '0' * 64), ('approval', {'native_step_execution': True}),
    ('capability_snapshot', {'forged': True}), ('job_id', 'other'), ('lease_token', ''), ('role', 'business')])
def test_native_actual_call_damage_does_not_pass_a_self_consistent_frozen_acceptance(monkeypatch, field, value):
    f = native(monkeypatch); f.call.data[field] = value
    with pytest.raises(HTTPException) as error: check(f)
    assert error.value.status_code == 409


@pytest.mark.parametrize('field,value', [('task_id', 'other'), ('plan_id', 'other'),
    ('run_id', 'other'), ('approval_hash', '0' * 64), ('actor_id', 'unrelated')])
def test_native_actual_job_damage_fails_closed(monkeypatch, field, value):
    f = native(monkeypatch); f.job.data[field] = value
    with pytest.raises(HTTPException): check(f)


def test_native_actual_plan_damage_cannot_be_hidden_by_unchanged_task_outcome(monkeypatch):
    f = native(monkeypatch); f.plan.data['execution_contract']['steps'][0]['input_snapshot']['value'] = 9
    with pytest.raises(HTTPException): check(f)


def test_old_acceptance_without_tools_or_modern_fields_remains_compatible(monkeypatch):
    f = legacy(monkeypatch)
    f.task.data['goal_spec']['criteria'] = [{'kind': 'human_confirmation'}]
    f.task.data['workflow_runtime']['receipts'] = []
    f.accepted = {'evaluation': {'completed': True}}
    f.task.data['native_goal_acceptance'] = copy.deepcopy(f.accepted); f.basis = None
    assert check(f) is None


def test_removing_frozen_native_results_does_not_downgrade_an_existing_execution_marker(monkeypatch):
    f = native(monkeypatch)
    f.accepted = {'evaluation': {'completed': True}}
    f.task.data['native_goal_acceptance'] = copy.deepcopy(f.accepted); f.basis = None
    with pytest.raises(HTTPException): check(f)


def stopped(monkeypatch, status='cancelled'):
    f = native(monkeypatch, cloud_original=True)
    f.run['status'] = status
    if status == 'rejected':
        f.run['review']['decision'] = 'rejected'
    else:
        f.run['history'].append({'operation': 'withdraw_adoption', 'previous_review': copy.deepcopy(f.run['review']),
            'actor_id': 'admin-stopper', 'reason': '停止此计划但保留原结果', 'at': AT})
    execution_ledger = f.task.data[execution.STATE]
    execution_ledger['ledger_hash'] = digest({key: value for key, value in execution_ledger.items() if key != 'ledger_hash'})
    stop = {'request_id': '00000000-0000-4000-8000-000000000001', 'actor_id': 'admin-stopper',
        'reason': '停止此计划但保留原结果', 'at': AT}
    f.plan.status = 'cancelled'
    f.plan.data.update(stop=stop, progress='原计划已停止；已发送请求或可能费用保留，晚到响应仅保存历史')
    f.plan.data['record_hash'] = digest({key: value for key, value in f.plan.data.items() if key != 'record_hash'})
    commands = [{'request_id': stop['request_id'], 'body_hash': 'e' * 64, 'actor_id': stop['actor_id'],
        'operation': 'stop', 'target': f.plan.id, 'plan_id': f.plan.id, 'abandoned': False, 'at': AT}]
    f.task.data[planning.STATE] = {'commands': commands, 'commands_hash': digest(commands)}
    audit = NS(id='stop-audit', workspace_id=f.p.workspace_id, actor_id=stop['actor_id'],
        object_id=f.plan.id, action='native_step_plan.stopped', data={'reason': stop['reason']})

    class StopReadDB(ReadOnlyDB):
        def scalar(self, statement):
            assert statement.column_descriptions[0]['entity'] is Audit
            assert statement._for_update_arg is None
            params = statement.compile().params
            assert f.p.workspace_id in params.values() and f.plan.id in params.values()
            assert 'native_step_plan.stopped' in params.values()
            return self.audit

    f.db = StopReadDB(); f.db.audit = audit
    accept(f, runs=[f.run]); install(monkeypatch, f)
    return f


@pytest.mark.parametrize('status', ['cancelled', 'rejected'])
def test_real_stop_record_shape_preserves_nonaccepted_runs_without_reconstructing_original_progress(monkeypatch, status):
    f = stopped(monkeypatch, status)
    before_task, before_plan = copy.deepcopy(f.task.data), copy.deepcopy(f.plan.data)
    assert check(f) is None
    assert f.task.data == before_task and f.plan.data == before_plan
    assert f.accepted['evaluation']['tool_results'] == []
    assert f.basis['execution_basis'] == f.accepted['evaluation']['tool_execution']['basis_hash']


def test_stopped_raw_model_titles_can_differ_from_original_approved_normalized_titles(monkeypatch):
    f = stopped(monkeypatch)
    raw = json.loads(f.plan.data['raw_response']['content'])
    approved = f.plan.data['proposal']['steps']
    assert all(before['title'] != after['title'] for before, after in zip(raw['steps'], approved))
    monkeypatch.setattr(planning, 'TITLES', {'report': '将来版本显示文案'})
    before = copy.deepcopy(f.plan.data)
    assert check(f) is None and f.plan.data == before


@pytest.mark.parametrize('damage', ['missing-stop', 'missing-audit', 'audit-actor', 'command-actor',
    'proposal', 'frozen', 'raw-answer', 'cloud-preview', 'cloud-receipt', 'cloud-job'])
def test_stop_exception_does_not_accept_resealed_immutable_changes_or_missing_original_stop_authority(monkeypatch, damage):
    f = stopped(monkeypatch)
    if damage == 'missing-stop': f.plan.data.pop('stop')
    elif damage == 'missing-audit': f.db.audit = None
    elif damage == 'audit-actor': f.db.audit.actor_id = 'wrong-actor'
    elif damage == 'command-actor':
        state = f.task.data[planning.STATE]; state['commands'][0]['actor_id'] = 'wrong-actor'
        state['commands_hash'] = digest(state['commands'])
    elif damage == 'proposal': f.plan.data['proposal']['steps'][0]['input_snapshot']['value'] = 9
    elif damage == 'frozen':
        frozen = f.plan.data['frozen']; frozen['messages'][0]['content'] = '另一个请求'
        frozen['preview_hash'] = digest({key: value for key, value in frozen.items() if key != 'preview_hash'})
    elif damage == 'raw-answer':
        raw = f.plan.data['raw_response']; answer = json.loads(raw['content']); answer['steps'][0]['arguments']['value'] = 9
        raw['content'] = json.dumps(answer); f.plan.data['raw_response_hash'] = digest(raw)
    elif damage == 'cloud-preview': f.cloud.data['native_step_plan']['preview_hash'] = '0' * 64
    elif damage == 'cloud-receipt': f.cloud.data['receipt']['request_id'] = 'other-response'
    else: f.source_job.data['task_id'] = 'other-task'
    f.plan.data['record_hash'] = digest({key: value for key, value in f.plan.data.items() if key != 'record_hash'})
    with pytest.raises(HTTPException) as error: check(f)
    assert error.value.status_code == 409
