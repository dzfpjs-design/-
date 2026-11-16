"""Pure authority/command tests; PG races and real HTTP are separate acceptance."""
import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from generic_agents.layered_approval_contract_v175 import (
    original_json, TaskCommand, ReviseCommand, PolicyCommand, next_state, independent)
from generic_agents import layered_approval_store_v175 as store

ID = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'
BASE = Path(__file__).resolve().parents[1] / 'src' / 'generic_agents'


def test_exact_utf8_original_and_whitespace_are_not_reconstructed():
    raw = ('{ "request_id": "' + ID + '", "expected_version": 2, "reason": "同意本轮\u00a0依据" }\n').encode()
    body, text, sha = original_json(raw, TaskCommand)
    assert body.expected_version == 2
    assert text.encode() == raw
    assert sha == hashlib.sha256(raw).hexdigest()
    assert sha != hashlib.sha256(json.dumps(body.model_dump(), ensure_ascii=False).encode()).hexdigest()


@pytest.mark.parametrize('extra', [
    '"expected_version": true', '"expected_version": "1"',
    '"expected_version": 0', '"expected_version": 1.0',
    '"expected_version": 1, "override": true',
    '"expected_version": 1, "expected_version": 2'])
def test_command_rejects_ambiguous_version_and_unknown_bypass(extra):
    raw = ('{"request_id":"' + ID + '","reason":"核对",' + extra + '}').encode()
    with pytest.raises(ValueError):
        original_json(raw, TaskCommand)


@pytest.mark.parametrize('reason', ['', '  ', '\x00'])
def test_reason_is_required_without_silently_trimming_original(reason):
    with pytest.raises(ValidationError):
        TaskCommand(request_id=ID, expected_version=1, reason=reason)


def test_nested_duplicate_keys_and_nonfinite_inputs_are_rejected():
    prefix = '{"request_id":"' + ID + '","expected_version":1,"reason":"修改",'
    for data in ['"value":{"title":"a","note":"","graph_inputs":{"x":1,"x":2},"goal_spec":null}}',
            '"value":{"title":"a","note":"","graph_inputs":{"x":NaN},"goal_spec":null}}']:
        with pytest.raises(ValueError):
            original_json((prefix + data).encode(), ReviseCommand)


@pytest.mark.parametrize('count', range(1, 21))
def test_configurable_serial_layers_do_not_start_or_accept_implicitly(count):
    state, stage = next_state('draft', 0, count, 'submit')
    for index in range(count):
        assert (state, stage) == ('in_review', index)
        state, stage = next_state(state, stage, count, 'approve')
    assert (state, stage) == ('approved', count)
    with pytest.raises(ValueError):
        next_state(state, stage, count, 'accept')
    assert next_state(state, stage, count, 'start') == ('executing', count)
    assert next_state('executing', count, count, 'accept') == ('accepted', count)


def test_return_and_withdraw_require_an_entire_new_round():
    assert next_state('in_review', 1, 3, 'return') == ('returned', 1)
    assert next_state('returned', 1, 3, 'submit') == ('in_review', 0)
    assert next_state('approved', 3, 3, 'withdraw') == ('withdrawn', 3)
    assert next_state('withdrawn', 3, 3, 'submit') == ('in_review', 0)
    with pytest.raises(ValueError):
        next_state('executing', 3, 3, 'withdraw')


@pytest.mark.parametrize('stage,count', [(True, 1), (0, True), (0, 0), (0, 21), (-1, 2), (3, 2)])
def test_closed_state_machine_rejects_invalid_layer_coordinates(stage, count):
    with pytest.raises(ValueError):
        next_state('draft', stage, count, 'submit')


def test_independent_acceptance_excludes_owner_approvers_and_actual_executors():
    for actor in ['owner', 'approver', 'executor']:
        with pytest.raises(ValueError):
            independent(actor, 'owner', ['approver'], ['executor'], acceptance=True)
    with pytest.raises(ValueError):
        independent('owner', 'owner')
    independent('acceptor', 'owner', ['approver'], ['executor'], acceptance=True)


def test_detail_offers_verified_formal_continuation_to_independent_acceptor(monkeypatch):
    from generic_agents.formal_continuation_contracts_v345 import INDEX
    p=NS(user_id='acceptor',role='operator');task=NS(id=ID,version=1,status='workflow_waiting',data={INDEX:[{'plan_id':'saved'}]})
    row=NS(id=OTHER,owner_id='owner',status='executing',version=1,policy_version_id=ID,policy_id=ID)
    data={'current_submission_id':None,'current_stage':0,'scope_id':ID,'draft_value':{},'draft_history':[],
          'submission_ids':[],'decisions':[],'decision_history':[],'execution':{},'executor_ids':[],
          'acceptance':None,'acceptance_history':[],'supplements':[]}
    policy={'stages':[],'executor':{},'acceptance':{}}
    monkeypatch.setattr(store,'managed',lambda *args,**kw:row)
    monkeypatch.setattr(store,'checked',lambda value: data if value is row else {'spec':policy})
    monkeypatch.setattr(store,'scoped',lambda db,model,*args: NS(enabled=True))
    monkeypatch.setattr(store,'real_member',lambda *args,**kw:None)
    monkeypatch.setattr(store,'role_allowed',lambda *args,**kw:None)
    proof={'basis_hash':'a'*64,'formal_continuation':{'simulation_only':True}}
    monkeypatch.setattr(store,'outcome',lambda *args,**kw:proof)
    view=store.detail(None,p,task)
    assert view['acceptance_basis']==proof and view['permissions']['accept']
    monkeypatch.setattr(store,'outcome',lambda *args,**kw:(_ for _ in ()).throw(HTTPException(409,'依据已失效')))
    stale=store.detail(None,p,task)
    assert stale['acceptance_basis'] is None and not stale['permissions']['accept'] and stale['acceptance_issue']=='依据已失效'


def test_policy_uses_real_scope_ids_and_is_not_fixed_to_two_levels():
    stage = {'name': '岗位', 'role_key': 'reviewer', 'scope_id': ID}
    base = {'request_id': ID, 'instance_id': OTHER, 'business_id': 'actual-business',
        'expected_version': 0, 'name': '逐级办理', 'scope_id': ID,
        'stages': [stage], 'executor': stage, 'acceptance': stage, 'enabled': True}
    assert len(PolicyCommand.model_validate(base).stages) == 1
    assert len(PolicyCommand.model_validate({**base, 'stages': [stage] * 20}).stages) == 20
    for stages in [[], [stage] * 21, [{**stage, 'scope_id': '某林场'}]]:
        with pytest.raises(ValidationError):
            PolicyCommand.model_validate({**base, 'stages': stages})


def managed_fixture():
    task = NS(id=ID, workspace_id=OTHER, package_id=OTHER, owner_id='owner',
        title='已保存任务', status='workflow_waiting', data={})
    row = NS(id=OTHER, workspace_id=OTHER, task_id=ID, policy_id=ID,
        policy_version_id=OTHER, owner_id='owner', status='draft', version=1, data={})
    store.seal(row, {'schema': store.SCHEMA, 'status': 'draft',
        'registration_audit_id': 'initial-audit',
        'context': store.context(task), 'context_hash': store.digest(store.context(task)),
        'command_ids': [], 'submission_ids': [], 'current_submission_id': None,
        'current_stage': 0, 'decisions': [], 'execution': None, 'executor_ids': [],
        'execution_actor_audit_ids': [],
        'draft_value': {'title': task.title, 'graph_inputs': {'quantity': 1}}})
    initial = store.authority(row)
    origin = NS(id='initial-audit', actor_id='owner', workspace_id=OTHER,
        object_id=ID, action='layered_approval.registered', data={
            'managed_id': OTHER, 'policy_id': ID, 'policy_version_id': OTHER,
            'context_hash': store.digest(store.context(task)), 'creation_hash': None,
            'initial_state': initial, 'initial_state_hash': store.digest(initial)})
    class DB:
        def __init__(self):
            self.info = {}; self.commands = []; self.audits = {origin.id: origin}
            self.actor_audits = []; self.step_audits = []
        def scalar(self, query):
            if query.column_descriptions[0]['entity'] is store.Audit:
                return origin
            return row
        def scalars(self, query):
            if query.column_descriptions[0]['entity'] is store.Command:
                return NS(all=lambda: self.commands)
            if query.column_descriptions[0]['entity'] is store.Audit:
                if 'layered_approval.execution_actor' in query.compile().params.values():
                    return NS(all=lambda: self.actor_audits)
                return NS(all=lambda: self.step_audits)
            return NS(all=lambda: [])
        def get(self, model, identity):
            return self.audits.get(identity)
        def add_all(self, values):
            for value in values:
                if isinstance(value, store.Command):
                    self.commands.append(value)
                else:
                    self.audits[value.id] = value
        def flush(self):
            pass
    return DB(), NS(workspace_id=OTHER, user_id='owner'), task, row


def test_permanent_managed_row_survives_task_json_marker_deletion():
    db, p, task, row = managed_fixture()
    assert 'layered_approval_v175' not in task.data
    assert store.managed(db, p, task) is row
    with pytest.raises(HTTPException, match='') as error:
        store.guard(db, p, task)
    assert error.value.status_code == 409


def test_task_status_or_title_cannot_replace_permanent_authority():
    db, p, task, row = managed_fixture()
    task.status = 'workflow_running'
    with pytest.raises(HTTPException):
        store.guard(db, p, task)
    task.title = '未获批准的新措施'
    with pytest.raises(HTTPException):
        store.managed(db, p, task)
    row.data['status'] = 'executing'
    with pytest.raises(HTTPException):
        store.checked(row)


def submitted_fixture():
    db, p, task, row = managed_fixture()
    previous = store.digest(store.authority(row))
    data = store.checked(row)
    data['status'] = 'in_review'; row.status = 'in_review'; row.version = 2
    store.seal(row, data)
    raw = json.dumps({'request_id': ID, 'expected_version': 1, 'reason': '原提交'})
    receipt = store.record(db, p, 'task_submit', task.id, ID, raw,
        hashlib.sha256(raw.encode()).hexdigest(), {
            'schema': 'layered-approval-task-receipt-v175', 'task_id': task.id,
            'managed_id': row.id, 'request_id': ID, 'operation': 'submit',
            'version': 2, 'task_version': 2, 'status': 'in_review', 'current_stage': 0},
        row, previous_state_hash=previous)
    store.verify_authority(db, p, task, row, db.commands)
    assert 'approval_state' not in receipt and 'previous_state_hash' not in receipt
    return db, p, task, row


@pytest.mark.parametrize('change', ['approved', 'stage', 'decisions', 'version', 'policy'])
def test_resealing_managed_state_cannot_forge_independent_command_history(change):
    db, p, task, row = submitted_fixture()
    independent_before = copy.deepcopy([c.data for c in db.commands])
    audit_before = copy.deepcopy({key: value.data for key, value in db.audits.items()})
    data = store.checked(row)
    if change == 'approved':
        row.status = data['status'] = 'approved'; data['current_stage'] = 2
    elif change == 'stage':
        data['current_stage'] = 2
    elif change == 'decisions':
        data['decisions'] = [{'actor_id': 'invented', 'decision': 'approve'}]
    elif change == 'version':
        row.version += 1
    else:
        row.policy_version_id = ID
    store.seal(row, data)  # Public checksum recomputation grants no authority.
    with pytest.raises(HTTPException) as error:
        store.verify_authority(db, p, task, row, db.commands)
    assert error.value.status_code == 409
    assert [c.data for c in db.commands] == independent_before
    assert {key: value.data for key, value in db.audits.items()} == audit_before


def test_command_order_and_runtime_enrollment_cannot_be_silently_deleted():
    db, p, task, row = submitted_fixture()
    data = store.checked(row)
    data['executor_ids'] = ['invented-acceptor-executor']
    store.seal(row, data)
    with pytest.raises(HTTPException):
        store.verify_authority(db, p, task, row, db.commands)


@pytest.mark.parametrize('replacement', [1.0, True])
def test_python_equal_but_different_json_types_cannot_reseal_approved_input(replacement):
    db, p, task, row = submitted_fixture()
    data = store.checked(row)
    data['draft_value']['graph_inputs']['quantity'] = replacement
    store.seal(row, data)
    with pytest.raises(HTTPException):
        store.verify_authority(db, p, task, row, db.commands)


def actor_enrollment_fixture():
    db, p, task, row = submitted_fixture()
    for nonce, operation, status in [(OTHER, 'decide', 'approved'),
            ('33333333-3333-3333-3333-333333333333', 'start', 'executing')]:
        previous = store.digest(store.authority(row)); data = store.checked(row)
        row.version += 1; row.status = data['status'] = status
        data['current_stage'] = 2
        if operation == 'start':
            data['execution'] = {'actor_id': 'starter', 'submission_hash': 'f' * 64, 'initialized': True}
            data['executor_ids'] = ['starter']
        store.seal(row, data)
        raw = json.dumps({'request_id': nonce, 'reason': operation})
        store.record(db, p, 'task_' + operation, task.id, nonce, raw,
            hashlib.sha256(raw.encode()).hexdigest(), {
                'task_id': task.id, 'managed_id': row.id, 'request_id': nonce,
                'operation': operation, 'version': row.version, 'status': status},
            row, previous_state_hash=previous)
    previous = store.digest(store.authority(row)); data = store.checked(row)
    actor = NS(id='actor-y', actor_id='actual-executor-y', data={})
    store.seal(actor, {'schema': 'layered-approval-execution-actor-v175',
        'managed_id': row.id, 'context_hash': data['context_hash'],
        'actor_id': actor.actor_id, 'submission_hash': 'f' * 64, 'at': 'saved'})
    db.actor_audits.append(actor)
    data['executor_ids'].append(actor.actor_id); data['execution_actor_audit_ids'].append(actor.id)
    row.version += 1; store.seal(row, data)
    nonce = '44444444-4444-4444-4444-444444444444'
    raw = json.dumps({'request_id': nonce, 'execution_actor_audit_id': actor.id})
    store.record(db, p, 'task_execution_actor', task.id, nonce, raw,
        hashlib.sha256(raw.encode()).hexdigest(), {
            'task_id': task.id, 'managed_id': row.id, 'request_id': nonce,
            'operation': 'execution_actor', 'version': row.version, 'status': row.status},
        row, previous_state_hash=previous)
    task.data['workflow_runtime'] = {'receipts': [{'actor': actor.actor_id}]}
    db.step_audits.append(NS(actor_id=actor.actor_id))
    store.verify_authority(db, p, task, row, db.commands)
    return db, p, task, row


@pytest.mark.parametrize('also_remove_enrollment', [False, True])
def test_execution_audit_single_or_compound_deletion_cannot_allow_self_acceptance(also_remove_enrollment):
    db, p, task, row = actor_enrollment_fixture()
    db.actor_audits.clear()
    if also_remove_enrollment:
        data = store.checked(row)
        data['executor_ids'].remove('actual-executor-y')
        data['execution_actor_audit_ids'].clear()
        store.seal(row, data)
    with pytest.raises(HTTPException) as error:
        store.verify_authority(db, p, task, row, db.commands)
    assert error.value.status_code == 409


def test_actual_step_receipts_cannot_bypass_actor_enrollment():
    db, p, task, row = managed_fixture()
    task.data['workflow_runtime'] = {'receipts': [{'actor': 'unregistered-executor'}]}
    with pytest.raises(HTTPException):
        store.verify_authority(db, p, task, row, db.commands)


def test_resealing_submission_cannot_change_frozen_inputs_or_executor_policy():
    submission = NS(id=ID, data={})
    original = {'value': {'title': '原措施'}, 'policy_snapshot': {'executor': 'original'},
        'policy_version_id': OTHER, 'context_hash': 'c' * 64}
    store.seal(submission, original)
    result = {'submission_hash': submission.data['record_hash'],
        'approval_state': {'policy_version_id': OTHER, 'data': {
            'current_submission_id': ID, 'draft_value': original['value'],
            'policy_snapshot': original['policy_snapshot'], 'context_hash': 'c' * 64}}}
    store.verify_submission(submission, store.checked(submission), result)
    for key, changed in [('value', {'title': '未审批新措施'}),
            ('policy_snapshot', {'executor': 'changed'}), ('policy_version_id', ID)]:
        modified = copy.deepcopy(original); modified[key] = changed
        store.seal(submission, modified)
        with pytest.raises(HTTPException):
            store.verify_submission(submission, store.checked(submission), result)


def test_internal_start_transition_token_is_always_removed_on_error():
    db, p, task, row = submitted_fixture()
    prior = store.digest(store.authority(row))
    approved = store.checked(row)
    row.version += 1; row.status = approved['status'] = 'approved'
    approved['current_stage'] = 2; store.seal(row, approved)
    raw = json.dumps({'request_id': OTHER, 'reason': '独立已保存决定'})
    store.record(db, p, 'task_decide', task.id, OTHER, raw,
        hashlib.sha256(raw.encode()).hexdigest(), {
            'schema': 'layered-approval-task-receipt-v175', 'task_id': task.id,
            'managed_id': row.id, 'request_id': OTHER, 'operation': 'decide',
            'version': row.version, 'task_version': 3, 'status': 'approved', 'current_stage': 2},
        row, previous_state_hash=prior)
    previous = store.digest(store.authority(row))
    data = store.checked(row)
    row.version += 1; row.status = data['status'] = 'executing'
    data['execution'] = {'actor_id': 'executor', 'submission_hash': 'f' * 64, 'initialized': False}
    data['executor_ids'] = ['executor']; store.seal(row, data)
    with pytest.raises(RuntimeError):
        with store.initializing(db, row, previous):
            store.verify_authority(db, p, task, row, db.commands)
            raise RuntimeError('no command commit')
    assert 'layered_approval_initializing_v175' not in db.info
    with pytest.raises(HTTPException):
        store.verify_authority(db, p, task, row, db.commands)


def test_original_receipt_requires_independent_matching_audit():
    raw = '{"original":"原正文"}'
    result = {'status': 'approved', 'version': 2}
    row = NS(id=ID, workspace_id=OTHER, actor_id='reviewer', request_id=ID,
        operation='task_decide', subject_id='task', exact_body=raw,
        body_sha256=hashlib.sha256(raw.encode()).hexdigest(), data={})
    evidence = {'command_id': ID, 'request_id': ID, 'operation': row.operation,
        'subject_id': 'task', 'body_sha256': row.body_sha256, 'result_hash': store.digest(result)}
    audit = NS(workspace_id=OTHER, actor_id='reviewer', action='layered_approval.command',
        object_id='task', data=copy.deepcopy(evidence))
    store.seal(row, {'schema': 'layered-approval-command-v175', 'result': result,
        'result_hash': store.digest(result), 'audit_id': 'audit', 'audit_hash': store.digest(evidence)})
    db = NS(get=lambda model, identity: audit)
    assert store.checked_command(db, row) == result
    audit.data['result_hash'] = '0' * 64
    with pytest.raises(HTTPException):
        store.checked_command(db, row)


def test_acceptance_hash_uses_evidence_content_not_inspection_clock():
    value = {'task_id': ID, 'task_version': 6, 'workflow_runtime': {'receipts': ['saved']},
        'evidence': {'evidence': [{'entity_id': OTHER, 'value': 12}], 'status': 'sufficient',
            'evaluated_at': '2026-10-23T00:00:00Z'}, 'evaluation': {'completed': True}}
    expected = store.acceptance_hash(value)
    inspected_again = copy.deepcopy(value)
    inspected_again['evidence']['evaluated_at'] = '2026-10-23T00:01:00Z'
    assert store.acceptance_hash(inspected_again) == expected
    inspected_again['evidence']['status'] = 'conflict'
    assert store.acceptance_hash(inspected_again) != expected
    inspected_again = copy.deepcopy(value)
    inspected_again['evidence']['evidence'][0]['value'] = 13
    assert store.acceptance_hash(inspected_again) != expected
    inspected_again = copy.deepcopy(value)
    inspected_again['task_version'] += 1
    assert store.acceptance_hash(inspected_again) != expected


def function(file, name):
    tree = ast.parse((BASE / file).read_text())
    return next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def calls(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and
        ((isinstance(n.func, ast.Name) and n.func.id == name) or
         (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def condition(node, *, goal, managed):
    """Evaluate only the two startup facts, independent of unparser spelling."""
    if isinstance(node, ast.Name) and node.id == 'approval_managed':
        return managed
    if isinstance(node, ast.Attribute) and node.attr == 'goal_spec' and isinstance(node.value, ast.Name) and node.value.id == 'body':
        return goal
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not condition(node.operand, goal=goal, managed=managed)
    if isinstance(node, ast.BoolOp):
        values = [condition(v, goal=goal, managed=managed) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    raise AssertionError('Startup guard contains an unaccounted authorization fact')


def test_registration_precedes_all_three_deferred_start_routes():
    node = function('workspace_api.py', 'create_task')
    registration = calls(node, 'register_approval')[0]
    assert registration.lineno < calls(node, 'define')[0].lineno
    assert registration.lineno < calls(node, 'initialize')[0].lineno
    assert registration.lineno < calls(node, 'continue_flow')[0].lineno
    definition = next(n for n in ast.walk(node) if isinstance(n, ast.If) and calls(n, 'define'))
    for goal in (False, True):
        for managed in (False, True):
            assert condition(definition.test, goal=goal, managed=managed) is (goal and not managed)
    deferred = next(n for n in ast.walk(node) if isinstance(n, ast.If)
        and isinstance(n.test, ast.Name) and n.test.id == 'approval_managed')
    managed_branch = ast.Module(body=deferred.body, type_ignores=[])
    legacy_branch = ast.Module(body=deferred.orelse, type_ignores=[])
    assert not calls(managed_branch, 'initialize') and not calls(managed_branch, 'continue_flow')
    assert calls(legacy_branch, 'initialize') and calls(legacy_branch, 'continue_flow')
    assert not calls(function('layered_approval_store_v175.py', 'register'), 'initialize')


@pytest.mark.parametrize('file,name,gate', [
    ('workspace_api.py', 'execution', 'approval_guard'),
    ('workflow_graph.py', 'initialize', 'approval_guard'),
    ('workflow_graph.py', 'settle', 'approval_guard'),
    ('workflow_graph.py', 'live', 'approval_guard'),
    ('workflow_graph.py', 'process', 'approval_guard'),
    ('workflow_graph_scope.py', 'before_dispatch', 'approval_guard'),
    ('workflow_abilities.py', 'schedule', 'approval_guard'),
    ('native_step_execution.py', 'before_dispatch', 'approval_guard'),
    ('native_goals.py', 'guard', 'approval_guard'),
    ('execution.py', 'execute_plan', 'approval_guard'),
    ('execution.py', 'accept_task', 'old_acceptance_guard')])
def test_real_execution_seams_call_permanent_gate(file, name, gate):
    assert calls(function(file, name), gate)


def test_ordinary_gateway_gate_is_after_running_receipt_commit_and_before_adapter():
    node = function('gateway.py', 'invoke')
    gate = calls(node, 'before_workflow_dispatch')[0]
    assert any(c.lineno < gate.lineno for c in calls(node, 'commit'))
    adapter = next(n for n in ast.walk(node) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'adapter' for t in n.targets)
        and n.lineno > gate.lineno)
    assert gate.lineno < adapter.lineno
    assert calls(function('layered_approval_store_v175.py', 'before_workflow_dispatch'), 'ownership')
    assert not calls(function('layered_approval_store_v175.py', 'prior'), 'guard')
    assert not calls(function('workspace_api.py', 'saved_execution'), 'approval_guard')


def test_approval_commands_keep_existing_parent_before_task_lock_order():
    node = function('layered_approval_store_v175.py', 'perform')
    assert calls(node, 'prior')[0].lineno < calls(node, 'prelock_task')[0].lineno
    assert calls(node, 'prelock_task')[0].lineno < calls(node, 'scoped')[0].lineno
    assert calls(node, 'scoped')[0].lineno < calls(node, 'managed')[0].lineno


def test_read_only_guard_uses_plain_selects_while_dispatch_retains_live_locks(monkeypatch):
    p = NS(user_id=ID, workspace_id=OTHER, role='operator')
    scope = NS(id=ID, name='甲范围', parent_id=None, enabled=True, data={})
    store.seal(scope, {'schema': 'approval-unit-v175', 'name': scope.name,
        'parent_id': None, 'enabled': True})
    grant = NS(id=OTHER, unit_id=ID, user_id=ID, role_key='executor', enabled=True, data={})
    store.seal(grant, {'schema': 'approval-role-v175', 'unit_id': ID,
        'user_id': ID, 'role_key': 'executor', 'enabled': True})
    row = NS(id=OTHER, policy_id=ID, status='executing', data={})
    store.seal(row, {'execution': {'actor_id': ID, 'submission_hash': 'f' * 64, 'initialized': True},
        'current_stage': 1, 'draft_value': {'goal_spec': None}, 'executor_ids': [ID]})
    spec = {'scope_id': ID, 'stages': [{}],
        'executor': {'scope_id': ID, 'role_key': 'executor'}}
    monkeypatch.setattr(store, 'managed', lambda *args, **kwargs: row)
    monkeypatch.setattr(store, 'policy_value', lambda *args, **kwargs: None)
    monkeypatch.setattr(store, 'current_submission', lambda *args: (None, {'value': {'goal_spec': None}}, spec))
    monkeypatch.setattr(store, 'row_hash', lambda *args: 'f' * 64)
    class SourceDB:
        def __init__(self, read_only):
            self.read_only = read_only; self.queries = []
        def scalar(self, query):
            self.queries.append(query)
            if self.read_only and query._for_update_arg is not None:
                raise AssertionError('Read-only snapshot attempted a row lock')
            return {store.Unit: scope, store.Role: grant,
                store.User: NS(id=ID), store.Policy: NS(id=ID)}[query.column_descriptions[0]['entity']]
    task = NS(data={'goal_spec': None})
    snapshot = SourceDB(True)
    store.guard(snapshot, p, task, lock_sources=False)
    assert snapshot.queries and all(q._for_update_arg is None for q in snapshot.queries)
    writer = SourceDB(False)
    store.guard(writer, p, task)
    assert writer.queries and all(q._for_update_arg is not None for q in writer.queries)
    with pytest.raises(HTTPException):
        store.guard(snapshot, p, task, record_actor=True, lock_sources=False)


def test_view_and_assessment_forward_read_mode_without_changing_write_defaults():
    node = function('native_goals.py', 'guard')
    forwarded = calls(node, 'approval_guard')[0]
    assert any(k.arg == 'lock_sources' and isinstance(k.value, ast.Name)
        and k.value.id == 'lock_sources' for k in forwarded.keywords)
    detail = function('layered_approval_store_v175.py', 'detail')
    assert any(k.arg == 'lock_sources' and isinstance(k.value, ast.Constant) and k.value.value is False
        for k in calls(detail, 'outcome')[0].keywords)
    assessment = function('native_goals.py', 'assessment')
    assert any(k.arg == 'lock_sources' and isinstance(k.value, ast.Name)
        and k.value.id == 'lock_sources' for k in calls(assessment, 'guard')[0].keywords)
    for file, name in [('layered_approval_store_v175.py', 'guard'),
            ('layered_approval_store_v175.py', 'outcome'), ('workspace_api.py', 'execution'),
            ('native_goals.py', 'guard'), ('native_goals.py', 'assessment')]:
        node = function(file, name)
        defaults = dict(zip((a.arg for a in node.args.kwonlyargs), node.args.kw_defaults))
        assert isinstance(defaults['lock_sources'], ast.Constant)
        assert defaults['lock_sources'].value is True
