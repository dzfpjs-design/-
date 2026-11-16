"""Pure frozen-representation tests, not synthetic DB execution authority.

The fixtures reproduce the two actual normalized receipt shapes and the saved
goal_gate proof formula. No current assessment, plan, DB, or provider is used.
"""
import copy

import pytest

from generic_agents.accepted_tool_basis_v129 import ERROR, accepted_tool_basis
from generic_agents.contracts import digest


AT = '2026-10-20T08:00:00+00:00'
H = 'a' * 64


def goal_spec(tool=False):
    criteria = [{'id': 'enough', 'kind': 'evidence_sufficient', 'description': '原资料满足要求'},
        {'id': 'human', 'kind': 'human_confirmation', 'description': '人工验收'}]
    if tool:
        criteria.append({'id': 'tool', 'kind': 'capability_success',
            'capability_id': 'workflow-capability', 'description': '原工具调用成功'})
    return {'purpose': '冻结依据，不声明现场事实', 'entity_ids': ['original-entity'],
        'required_fields': ['资料'], 'criteria': criteria}


def workflow_result(suffix='one', *, output=None):
    return {'status': 'succeeded', 'method': 'registered_capability',
        'capability_id': 'workflow-capability', 'capability_name': '资料核对能力',
        'capability_hash': H, 'invocation_id': 'workflow-call-' + suffix,
        'input_hash': H, 'output': {'value': 1} if output is None else copy.deepcopy(output),
        'workflow_step_id': 'original-step-' + suffix, 'workflow_receipt_hash': digest(suffix),
        'candidate_only': True, 'physical_state_verified': False}


def execution_run(identity='plan-a', status='accepted'):
    review = None if status == 'cancelled' else {'decision': status,
        'reason': '对照原结果核查', 'actor_id': 'original-reviewer', 'at': AT}
    return {'id': identity, 'version': 3, 'actor_id': 'original-actor',
        'step_id': 'original-human-step', 'status': status, 'approval_hash': H,
        'result_hash': None if status == 'cancelled' else digest(identity),
        'review': review, 'history': [{'operation': 'retained_original_history'}],
        'started_steps': [], 'steps': [], 'job_ids': [], 'progress': '原冻结进度',
        'error': None, 'possible_charge': False}


def execution_result(spec, run, suffix='one', *, output=None):
    output = {'value': 2} if output is None else copy.deepcopy(output)
    return {'status': 'succeeded', 'method': 'registered_capability',
        'capability_id': 'explicit-native-tool', 'output': output,
        'invocation_id': run['id'] + '-call-' + suffix, 'input_hash': H,
        'output_hash': digest(output), 'capability_hash': H, 'release_version': '1.0.0',
        'plan_id': run['id'], 'run_id': run['id'], 'approval_hash': run['approval_hash'],
        'result_hash': run['result_hash'], 'goal_snapshot': {'version': 1,
            'hash': digest(spec), 'spec': copy.deepcopy(spec), 'time': {'mode': 'archive'}}}


def frozen_evaluation(*, workflow=(), runs=(), native=()):
    """Independent fixture construction from native_step_execution.goal_gate."""
    proofs = []
    for run in runs:
        proof = {'plan_id': run['id'], 'version': run['version'], 'status': run['status'],
            'approval_hash': run['approval_hash'], 'result_hash': run['result_hash'],
            'review': copy.deepcopy(run['review']), 'history_hash': digest(run['history']),
            'started_steps_hash': digest(run['started_steps'])}
        if run['status'] == 'accepted':
            proof['verified_receipts_hash'] = digest([item for item in native if item['plan_id'] == run['id']])
        proofs.append(proof)
    return {'completed': True, 'flow_finished': True, 'data_received': True,
        'tool_results': copy.deepcopy([*workflow, *native]),
        'tool_execution': {'all_reviewed': True, 'runs': copy.deepcopy(list(runs)),
            'basis_hash': digest(proofs)}}


def test_mixed_results_recover_only_workflow_prefix_in_original_order():
    spec = goal_spec(True)
    workflow = [workflow_result('z'), workflow_result('a')]
    runs = [execution_run('plan-a'), execution_run('plan-z')]
    native = [execution_result(spec, runs[0], 'z'), execution_result(spec, runs[0], 'a'),
        execution_result(spec, runs[1])]
    evaluation = frozen_evaluation(workflow=workflow, runs=runs, native=native)
    result = accepted_tool_basis(spec, evaluation)
    assert result == {'workflow_results': workflow,
        'execution_basis': evaluation['tool_execution']['basis_hash']}
    assert result['workflow_results'] != evaluation['tool_results']


@pytest.mark.parametrize('status', ['accepted', 'rejected', 'cancelled'])
def test_runs_only_terminal_history_still_adds_execution_basis_without_tool_criteria(status):
    spec = goal_spec()
    evaluation = frozen_evaluation(runs=[execution_run(status=status)])
    assert evaluation['tool_results'] == []
    assert accepted_tool_basis(spec, evaluation) == {'workflow_results': [],
        'execution_basis': evaluation['tool_execution']['basis_hash']}


def test_native_only_actual_receipt_shape_is_not_misclassified_as_workflow():
    spec = goal_spec(); run = execution_run()
    evaluation = frozen_evaluation(runs=[run], native=[execution_result(spec, run)])
    assert accepted_tool_basis(spec, evaluation)['workflow_results'] == []


def test_legacy_no_tool_and_modern_empty_keep_original_absence_of_third_argument():
    spec = goal_spec()
    assert accepted_tool_basis(spec, {'completed': True}) is None
    assert accepted_tool_basis(spec, frozen_evaluation()) is None
    assert digest({'evidence': 'frozen'}) != digest({'evidence': 'frozen', 'tool_results': None})


def test_explicit_tool_criterion_uses_tool_basis_even_before_any_result():
    # Reproduce has_tools exactly; this helper does not certify completion.
    evaluation = frozen_evaluation()
    assert accepted_tool_basis(goal_spec(True), evaluation) == {
        'workflow_results': [], 'execution_basis': digest([])}


def test_original_json_numbers_unicode_and_boolean_outputs_are_preserved():
    spec = goal_spec(True)
    output = {'资料𠀀': [1.0, -0.0, False, {'\ue000': '原文结构'}]}
    evaluation = frozen_evaluation(workflow=[workflow_result(output=output)])
    original = copy.deepcopy((spec, evaluation))
    result = accepted_tool_basis(spec, evaluation)
    assert digest(result['workflow_results'][0]['output']) == digest(output)
    assert type(result['workflow_results'][0]['output']['资料𠀀'][0]) is float
    result['workflow_results'][0]['output']['资料𠀀'].append('外部修改')
    assert (spec, evaluation) == original
    assert digest((spec, evaluation)) == digest(original)


@pytest.mark.parametrize('value', [False, [1, True, {'value': 2.0}], '原输出', 3])
def test_capability_success_does_not_invent_an_object_only_output_contract(value):
    evaluation = frozen_evaluation(workflow=[workflow_result(output=value)])
    assert accepted_tool_basis(goal_spec(True), evaluation)['workflow_results'][0]['output'] == value


@pytest.mark.parametrize('field', ['tool_results', 'tool_execution'])
def test_partial_modern_fields_cannot_fall_back_to_legacy(field):
    evaluation = frozen_evaluation(); del evaluation[field]
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(goal_spec(), evaluation)


@pytest.mark.parametrize('field', ['workflow_results', 'execution_basis', 'tool_basis'])
def test_unknown_tool_alias_is_not_a_legacy_no_tool_acceptance(field):
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(goal_spec(), {'completed': True, field: []})


def test_legacy_missing_tool_freeze_cannot_be_reconstructed_from_tool_criteria():
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(goal_spec(True), {'completed': True})


@pytest.mark.parametrize('target,value', [
    ('tool_results', False), ('tool_results', {}), ('tool_results', ()),
    ('tool_execution', False), ('tool_execution', []),
    ('runs', False), ('runs', {}), ('runs', ()),
    ('all_reviewed', 1), ('all_reviewed', False),
    ('basis_hash', True), ('basis_hash', 'A' * 64), ('basis_hash', [H]),
])
def test_frozen_container_bool_and_hash_type_traps_are_rejected(target, value):
    evaluation = frozen_evaluation()
    if target in {'tool_results', 'tool_execution'}:
        evaluation[target] = value
    else:
        evaluation['tool_execution'][target] = value
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(goal_spec(), evaluation)


@pytest.mark.parametrize('field', ['all_reviewed', 'runs', 'basis_hash'])
def test_missing_execution_field_is_not_defaulted(field):
    evaluation = frozen_evaluation(); del evaluation['tool_execution'][field]
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(goal_spec(), evaluation)


@pytest.mark.parametrize('mode', ['both', 'neither', 'extra', 'missing', 'candidate_int', 'physical_int'])
def test_receipt_family_is_closed_and_unambiguous(mode):
    spec = goal_spec(True); value = workflow_result()
    if mode == 'both': value.update(plan_id='plan-a', run_id='plan-a')
    elif mode == 'neither':
        del value['workflow_step_id']; del value['workflow_receipt_hash']
    elif mode == 'extra': value['new_receipt_family'] = True
    elif mode == 'missing': del value['input_hash']
    elif mode == 'candidate_int': value['candidate_only'] = 1
    else: value['physical_state_verified'] = 0
    evaluation = frozen_evaluation(workflow=[value])
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(spec, evaluation)


@pytest.mark.parametrize('field', ['id', 'version', 'status', 'approval_hash', 'result_hash',
    'review', 'history', 'started_steps'])
def test_run_proof_fields_are_required_even_without_invocations(field):
    run = execution_run(); evaluation = frozen_evaluation(runs=[run])
    del evaluation['tool_execution']['runs'][0][field]
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(goal_spec(), evaluation)


@pytest.mark.parametrize('field,value', [('version', True), ('status', 'queued'),
    ('history', False), ('started_steps', {}), ('review', None),
    ('approval_hash', True), ('result_hash', None), ('scope_error', 'not an accepted run')])
def test_unsettled_or_malformed_run_never_becomes_a_frozen_accepted_gate(field, value):
    evaluation = frozen_evaluation(runs=[execution_run()])
    evaluation['tool_execution']['runs'][0][field] = value
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(goal_spec(), evaluation)


@pytest.mark.parametrize('damage', ['run_order', 'duplicate_run', 'run_history', 'execution_hash',
    'receipt_order', 'late_workflow', 'drop_native', 'output', 'plan', 'approval', 'snapshot'])
def test_deleted_changed_or_reordered_frozen_execution_parts_are_rejected(damage):
    spec = goal_spec(True); a, z = execution_run('plan-a'), execution_run('plan-z')
    evaluation = frozen_evaluation(workflow=[workflow_result()], runs=[a, z],
        native=[execution_result(spec, a), execution_result(spec, z)])
    runs = evaluation['tool_execution']['runs']; receipts = evaluation['tool_results']
    if damage == 'run_order': runs.reverse()
    elif damage == 'duplicate_run': runs.append(copy.deepcopy(runs[-1]))
    elif damage == 'run_history': runs[0]['history'].append({'operation': 'changed'})
    elif damage == 'execution_hash': evaluation['tool_execution']['basis_hash'] = 'b' * 64
    elif damage == 'receipt_order': receipts[1:] = reversed(receipts[1:])
    elif damage == 'late_workflow': receipts.append(receipts.pop(0))
    elif damage == 'drop_native': receipts.pop()
    elif damage == 'output': receipts[1]['output']['value'] += 1
    elif damage == 'plan': receipts[1]['plan_id'] = 'unknown-plan'
    elif damage == 'approval': receipts[1]['approval_hash'] = 'c' * 64
    else: receipts[1]['goal_snapshot']['spec']['purpose'] = '替换目标'
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(spec, evaluation)


def test_workflow_value_change_changes_basis_input_instead_of_backsolving_old_hash():
    spec = goal_spec(True); evaluation = frozen_evaluation(workflow=[workflow_result()])
    original = accepted_tool_basis(spec, evaluation)
    evaluation['tool_results'][0]['output']['value'] = 999
    changed = accepted_tool_basis(spec, evaluation)
    assert digest(original) != digest(changed)
    # The caller compares this changed input to the actual frozen acceptance
    # basis and separately verifies original DB receipts. This pure function
    # cannot claim that a syntactically valid output actually came from a model.


@pytest.mark.parametrize('spec,evaluation', [(False, {}), ({'criteria': False}, {}),
    ({'criteria': []}, {}), ({'criteria': [True]}, {}),
    ({'criteria': [{'kind': ['tool_value']}]}, {}),
    ({'criteria': [{'kind': 'capability_success', 'capability_id': True}]}, {})])
def test_goal_spec_is_not_coerced_or_guessed(spec, evaluation):
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(spec, evaluation)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), {1: 'not a JSON key'}, {'x': {1}}, '\ud800'])
def test_non_json_frozen_output_is_rejected_without_echoing_its_value(value):
    evaluation = frozen_evaluation(workflow=[workflow_result(output=value)])
    with pytest.raises(ValueError) as caught:
        accepted_tool_basis(goal_spec(True), evaluation)
    assert str(caught.value) == ERROR


def test_cyclic_input_is_rejected_and_preserved():
    evaluation = frozen_evaluation(); evaluation['cycle'] = evaluation
    with pytest.raises(ValueError, match=ERROR):
        accepted_tool_basis(goal_spec(), evaluation)
    assert evaluation['cycle'] is evaluation
