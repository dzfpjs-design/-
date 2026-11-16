"""Pure synthetic runtime contracts; no account/source/PG/runtime authority proof.

Root runs these tests and the separate PostgreSQL/API acceptance journey. Local
stubs below exercise rejection/contract boundaries only, never real permission.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID

from fastapi import HTTPException
import pytest

from generic_agents.contracts import digest
from generic_agents import report_full_semantic_runtime_v2 as runtime
from generic_agents import report_full_semantic_runtime_adoption as adoption
from tests.test_report_full_semantic_schema_v1_pure import inputs as source_inputs, uid, seal


def single(result):
    result['global_claims'] = []; result['questions'] = []
    business = result['businesses'][0]
    business['steps'] = business['steps'][:1]
    business['steps'][0].update(next_index=None, else_index=None, branch_condition='')
    for key in ('participants', 'interactions', 'conditions', 'coordination'):
        business[key] = []


def inputs(change=None):
    def configure(result):
        single(result)
        if change is not None: change(result)
    design, manifest, proof, _, _ = source_inputs(configure)
    actor = {'user_id': uid(9001), 'username': 'synthetic_operator', 'membership_role': 'operator'}
    return design, manifest, proof, design['result']['businesses'][0]['name'], actor


def build(values=None):
    return runtime.build_runtime_binding(*(inputs() if values is None else values), proof_id=uid(9002))


def snapshot(values=None, *, issues=None):
    binding = build(values); design = binding['source_design']; name = binding['business_name']
    origin = runtime.build_origin(uid(9003), binding)
    businesses = [{'id': uid(9004 + index), 'name': business['name'],
        'flow': runtime.semantic.derive_flows(design)[business['name']],
        'issues': list(issues or []) if index == binding['candidate_index'] else ['unsupported'],
        'available': not issues if index == binding['candidate_index'] else False,
        'execution_mode': 'confirmed_human_workflow' if index == binding['candidate_index'] and not issues else 'blocked',
        **({runtime.BUSINESS_MARKER: copy.deepcopy(origin)} if index == binding['candidate_index'] else {})}
        for index, business in enumerate(design['result']['businesses'])]
    roles = {origin['role']: {'user_id': origin['actor_id'], 'username': origin['actor_username']}}
    plan = runtime.plan_value([design], businesses, [{'origin': origin, 'binding': binding}], roles)
    return {'businesses': businesses, 'roles': roles, 'semantic_runtime_plan': plan}


def test_single_step_source_is_unchanged_and_execution_scope_is_manual_only():
    values = inputs(); before = copy.deepcopy(values); binding = build(values)
    assert binding['source_design'] == values[0] and binding['source_business'] == values[0]['result']['businesses'][0]
    assert binding['source_manifest'] == values[1] and binding['source_proof'] == values[2]
    assert binding['source_anchor'] == values[2]['selection']['anchor']
    assert binding['semantic_mode'] == 'workspace_workflow_design'
    assert binding['source_semantics_status'] == 'not_certified'
    assert binding['trigger_mode'] == 'manual' and binding['runtime_policy'] == runtime.POLICY
    assert runtime.check_runtime_binding(binding) == binding == build(values)
    assert values == before
    binding['source_business']['steps'][0]['name'] = 'only returned copy'
    assert values == before


@pytest.mark.parametrize('agent', ['人工确认', '核对职责'])
def test_only_the_single_human_participant_may_match_the_source_step(agent):
    values = inputs(lambda result: result['businesses'][0]['participants'].append({
        'agent': agent, 'responsibility': '人工核对职责',
        'evidence': {'status': 'explicit', 'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': ''}}))
    assert build(values)['source_business']['participants'][0]['agent'] == agent


def two_steps(result):
    business = result['businesses'][0]
    other = copy.deepcopy(business['steps'][0]); other['name'] = '第二个人工步骤'
    business['steps'].append(other); business['steps'][0]['next_index'] = 1


@pytest.mark.parametrize('change', [
    two_steps,
    lambda result: result['businesses'][0]['steps'][0].update(branch_condition='原文条件'),
    lambda result: result['businesses'][0]['steps'][0].update(agent='业务智能体', role=''),
    lambda result: result['businesses'][0]['conditions'].append({'kind': 'precondition',
        'statement': '独立前提', 'step_indices': [0],
        'evidence': {'status': 'explicit', 'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': ''}}),
    lambda result: result['businesses'][0]['participants'].append({'agent': '其他智能体',
        'responsibility': '另一职责', 'evidence': {'status': 'explicit',
            'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': ''}}),
    lambda result: result['questions'].append('独立未决问题'),
    lambda result: result['global_claims'].append({'kind': '治理', 'statement': '独立全局约束',
        'span_refs': ['w:p1/s1'], 'status': 'explicit', 'explanation': ''}),
])
def test_unsupported_semantics_are_rejected_instead_of_being_flattened(change):
    values = inputs(change); before = copy.deepcopy(values)
    with pytest.raises(ValueError): build(values)
    assert values == before


def test_parallel_and_interactions_remain_explicitly_unsupported():
    def parallel(result):
        two_steps(result)
        result['businesses'][0]['coordination'].append({'kind': 'parallel', 'step_indices': [0, 1],
            'start_rule': '同时开始', 'wait_rule': '各自等待', 'join_rule': '全部完成',
            'evidence': {'status': 'explicit', 'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': ''}})
    with pytest.raises(ValueError): build(inputs(parallel))
    def interaction(result):
        business = result['businesses'][0]
        business['participants'] = [{'agent': agent, 'responsibility': '原职责',
            'evidence': {'status': 'explicit', 'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': ''}}
            for agent in ['人工确认', '核对职责']]
        business['interactions'] = [{'from_participant_index': 0, 'to_participant_index': 1,
            'purpose': '原交接', 'condition': '', 'step_indices': [0],
            'evidence': {'status': 'explicit', 'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': ''}}]
    with pytest.raises(ValueError): build(inputs(interaction))


@pytest.mark.parametrize('change', [
    lambda value: value.update(extra=True), lambda value: value.update(trigger_mode='automatic'),
    lambda value: value.update(semantic_mode='source_fact_approval'),
    lambda value: value['executor'].update(membership_role='viewer'),
    lambda value: value['source_business']['steps'][0].update(name='rewritten'),
    lambda value: value['source_anchor'].update(generation=99),
    lambda value: value['source_manifest'].update(design_hash='0' * 64),
    lambda value: value['source_proof'].update(can_publish=True),
])
def test_hash_reseal_cannot_authorize_changed_scope_or_source(change):
    value = build(); change(value); seal(value, 'binding_hash')
    with pytest.raises(ValueError): runtime.check_runtime_binding(value)


def test_plan_cold_checks_roles_and_keeps_source_confirmations_unset():
    value = snapshot(); before = copy.deepcopy(value)
    assert runtime.verify_plan(value) == value['semantic_runtime_plan']
    assert runtime.pins(value) == {'full_semantic_runtime_schema': runtime.SCHEMA,
        'full_semantic_runtime_hash': value['semantic_runtime_plan']['plan_hash']}
    flow = value['businesses'][0]['flow']
    assert flow['confirmedAt'] is None and flow['confirmedSignature'] is None
    assert flow['fullSemanticSource']['design_hash'] == value['semantic_runtime_plan']['designs'][0]['design_hash']
    assert value == before


@pytest.mark.parametrize('mode', ['missing-role', 'other-user', 'other-username', 'extra-role-data',
    'confirmed-signature', 'confirmed-at', 'step-extra-execution', 'plan-marker-removed', 'business-marker-removed',
    'available-label', 'runtime-version-mixed'])
def test_bad_roles_confirmations_or_partial_markers_fail_without_mutation(mode):
    value = snapshot(); business = value['businesses'][0]; role = next(iter(value['roles']))
    if mode == 'missing-role': value['roles'] = {}
    elif mode == 'other-user': value['roles'][role]['user_id'] = uid(9900)
    elif mode == 'other-username': value['roles'][role]['username'] = 'different'
    elif mode == 'extra-role-data': value['roles'][role]['granted'] = True
    elif mode == 'confirmed-signature': business['flow']['confirmedSignature'] = '{}'
    elif mode == 'confirmed-at': business['flow']['confirmedAt'] = '2026-01-01'
    elif mode == 'step-extra-execution': business['flow']['steps'][0]['execution'] = {'adapter': 'builtin'}
    elif mode == 'plan-marker-removed': value.pop('semantic_runtime_plan')
    elif mode == 'business-marker-removed': business.pop(runtime.BUSINESS_MARKER)
    elif mode == 'available-label': business['execution_mode'] = 'registered_ability_workflow'
    else: value['semantic_plan'] = {}
    if 'semantic_runtime_plan' in value: seal(value['semantic_runtime_plan'], 'plan_hash')
    before = copy.deepcopy(value)
    with pytest.raises(ValueError): runtime.verify_plan(value)
    assert value == before


def test_unsupported_sibling_design_is_preserved_and_stays_blocked():
    def sibling(result):
        business = copy.deepcopy(result['businesses'][0]); business['name'] = '复杂兄弟业务'
        step = copy.deepcopy(business['steps'][0]); step['name'] = '另一步'
        business['steps'].append(step); business['steps'][0]['next_index'] = 1
        result['businesses'].append(business)
    value = snapshot(inputs(sibling)); plan = runtime.verify_plan(value)
    assert len(plan['designs'][0]['result']['businesses']) == 2
    assert plan['business_bindings'][1]['adoption_id'] is None
    value['businesses'][1].update(available=True, execution_mode='confirmed_human_workflow')
    with pytest.raises(ValueError): runtime.verify_plan(value)


def test_existing_business_issues_remain_blocking_instead_of_being_cleared():
    value = snapshot(issues=['正式结构采用尚未就绪'])
    assert runtime.verify_plan(value)
    assert value['businesses'][0]['available'] is False
    assert value['businesses'][0]['issues'] == ['正式结构采用尚未就绪']


def ledger():
    binding = build(); owner = NS(user_id=uid(9100), workspace_id=uid(9101))
    name = binding['business_name']; design = binding['source_design']
    before = {'fullSemanticDesigns': [design], 'flows': runtime.semantic.derive_flows(design)}
    command = adoption.Preview(schema='report-full-semantic-runtime-preview-command-v2',
        configuration_revision=5, configuration_hash=digest(before), proof_id=UUID(binding['proof_id']),
        business_name=name, actor_id=UUID(binding['executor']['user_id']))
    preview = adoption.make_preview(owner, design['handoff_id'], command, binding)
    body = adoption.Adopt.model_validate({**command.model_dump(mode='json'),
        'schema': 'report-full-semantic-runtime-command-v2', 'request_id': uid(9102),
        'preview_hash': preview['preview_hash'], 'consent': True, 'semantic_mode': runtime.SEMANTIC_MODE})
    identity = adoption.adoption_id(owner, design['handoff_id'], body.request_id)
    origin = runtime.build_origin(identity, binding)
    after = copy.deepcopy(before); after[runtime.CONFIG_KEY] = {name: origin}
    record = seal({'schema': adoption.RECORD, 'id': identity, 'workspace_id': owner.workspace_id,
        'actor_id': owner.user_id, 'target_actor_id': binding['executor']['user_id'],
        'handoff_id': design['handoff_id'], 'configuration_id': uid(9103), 'request_id': str(body.request_id),
        'business_name': name, 'proof_id': binding['proof_id'], 'binding': binding, 'origin': origin,
        'command_hash': adoption.command_hash(owner, design['handoff_id'], body), 'preview_hash': body.preview_hash,
        'accepted_revision': 6, 'configuration_hash': digest(after)}, 'record_hash')
    data = seal({'schema': adoption.LEDGER, 'record': record, 'command': body.model_dump(mode='json'),
        'receipt': adoption.make_receipt(record)}, 'ledger_hash')
    row = NS(**{key: record[key] for key in ['id', 'workspace_id', 'actor_id', 'target_actor_id',
        'handoff_id', 'configuration_id', 'request_id', 'business_name', 'proof_id']}, version=1, data=data)
    return owner, row, before, after


@pytest.mark.parametrize('consent', [False, 1, 'true', None])
def test_command_requires_separate_strict_true_workflow_design_consent(consent):
    _, row, _, _ = ledger(); command = copy.deepcopy(row.data['command']); command['consent'] = consent
    with pytest.raises(ValueError): adoption.Adopt.model_validate(command)


def test_preview_retains_full_business_and_does_not_relabel_position_proof_as_semantic_consent():
    owner, row, _, _ = ledger(); body = adoption.Adopt.model_validate(row.data['command'])
    preview = adoption.make_preview(owner, row.handoff_id, body, row.data['record']['binding'])
    assert preview['source_business'] == row.data['record']['binding']['source_business']
    assert preview['source_semantics_status'] == 'not_certified'
    assert preview['execution_boundary']['trigger_mode'] == 'manual'
    assert preview['binding']['source_proof']['can_publish'] is False
    assert adoption.checked_ledger(row, owner, private=True) == row.data


def test_forged_unconsented_ledger_fails_even_when_outer_hashes_are_resealed():
    owner, row, _, _ = ledger(); row.data['command']['consent'] = False; seal(row.data, 'ledger_hash')
    with pytest.raises(HTTPException) as error: adoption.checked_ledger(row, owner)
    assert error.value.status_code == 409


def test_accepted_history_requires_exact_actual_before_and_after_configurations():
    owner, row, before, after = ledger()
    class HistoryDB:
        def __init__(self, values): self.values = iter(values)
        def scalar(self, query): return NS(data=next(self.values))
    assert adoption.accepted_history(HistoryDB([before, after]), owner, row, row.data) == after
    tampered = copy.deepcopy(after); tampered['unrelated'] = 'not in the adopted transaction'
    with pytest.raises(HTTPException):
        adoption.accepted_history(HistoryDB([before, tampered]), owner, row, row.data)


def test_unrelated_saves_need_real_history_but_do_not_require_current_source_or_actor(monkeypatch):
    owner, row, _, after = ledger()
    class HistoryDB:
        def get(self, model, identity): assert identity == row.id; return row
    monkeypatch.setattr(adoption, 'accepted_history', lambda *args: copy.deepcopy(after))
    def no_live_gate(*args): raise AssertionError('Unrelated retention must not ask for live authority')
    monkeypatch.setattr(adoption, 'verify_origin', no_live_gate)
    proposed = copy.deepcopy(after); proposed['unrelated'] = 'editable while the source is blocked'
    assert adoption.protect_configuration(HistoryDB(), owner, after, proposed) == proposed
    removed = copy.deepcopy(proposed); removed[runtime.CONFIG_KEY] = {}
    with pytest.raises(HTTPException): adoption.protect_configuration(HistoryDB(), owner, after, removed)
